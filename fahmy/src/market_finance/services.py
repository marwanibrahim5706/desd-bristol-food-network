from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from market_orders.models import Order, OrderItem, ProducerSubOrder
from market_payments.models import Payment as PaymentSnapshot, Settlement
from market_products.models import Product

from .models import RecurringOrder

FINANCE_PROCESSED_STATUSES = {
    ProducerSubOrder.Status.CONFIRMED,
    ProducerSubOrder.Status.READY,
    ProducerSubOrder.Status.DELIVERED,
}

PAYOUT_API_PROVIDER = "external_payout_api"


@dataclass(frozen=True)
class PayoutApiResult:
    success: bool
    reference: str = ""
    message: str = ""


def apply_finance_filters(queryset, *, status_filter="", producer_filter="", q="", date_from="", date_to=""):
    if status_filter:
        queryset = queryset.filter(status=status_filter)

    if producer_filter:
        queryset = queryset.filter(producer_id=producer_filter)

    if q:
        queryset = queryset.filter(
            Q(order__id__icontains=q)
            | Q(order__customer__username__icontains=q)
            | Q(order__customer__email__icontains=q)
            | Q(producer__username__icontains=q)
            | Q(producer__business_name__icontains=q)
        )

    if date_from:
        queryset = queryset.filter(delivery_date__date__gte=date_from)

    if date_to:
        queryset = queryset.filter(delivery_date__date__lte=date_to)

    return queryset


def base_finance_queryset():
    return (
        ProducerSubOrder.objects.select_related("order", "order__customer", "producer")
        .prefetch_related("items")
        .order_by("-delivery_date", "-id")
    )


def aggregate_finance_totals(queryset):
    aggregates = queryset.aggregate(
        gross_sales=Sum("subtotal"),
        total_commission=Sum("commission_amount"),
        total_payouts=Sum("producer_payout_amount"),
    )
    return {
        "gross_sales": aggregates["gross_sales"] or Decimal("0.00"),
        "total_commission": aggregates["total_commission"] or Decimal("0.00"),
        "total_payouts": aggregates["total_payouts"] or Decimal("0.00"),
    }


def count_processed_orders(suborders):
    return len(
        {
            suborder.order_id
            for suborder in suborders
            if suborder.status in FINANCE_PROCESSED_STATUSES
        }
    )


def build_order_finance_summaries(suborders):
    """
    Group producer suborders into order-level finance summaries for audit views.
    """
    grouped = {}
    for suborder in suborders:
        summary = grouped.setdefault(
            suborder.order_id,
            {
                "order": suborder.order,
                "customer": suborder.order.customer,
                "suborders": [],
                "order_total": Decimal("0.00"),
                "commission_total": Decimal("0.00"),
                "producer_payout_total": Decimal("0.00"),
                "supplier_count": 0,
                "statuses": set(),
            },
        )
        summary["suborders"].append(suborder)
        summary["order_total"] += suborder.subtotal
        summary["commission_total"] += suborder.commission_amount
        summary["producer_payout_total"] += suborder.producer_payout_amount
        summary["supplier_count"] += 1
        summary["statuses"].add(suborder.status)

    summaries = list(grouped.values())
    for summary in summaries:
        summary["suborders"].sort(key=lambda suborder: (suborder.producer.username.lower(), suborder.id))
        summary["status_summary"] = ", ".join(sorted(summary["statuses"]))
        summary["settlement_status"] = _summarize_order_settlement_status(summary["suborders"])
    return sorted(summaries, key=lambda summary: (summary["order"].created_at, summary["order"].id), reverse=True)


def _summarize_order_settlement_status(suborders):
    delivered_suborders = [suborder for suborder in suborders if suborder.status == ProducerSubOrder.Status.DELIVERED]
    if not delivered_suborders:
        return "Awaiting delivered suborders"

    settlement_map = {}
    settlement_qs = (
        Settlement.objects.filter(
            producer_id__in={suborder.producer_id for suborder in delivered_suborders},
            included_suborders__in=delivered_suborders,
        )
        .distinct()
        .prefetch_related("included_suborders")
    )
    for settlement in settlement_qs:
        for included_id in settlement.included_suborders.values_list("id", flat=True):
            settlement_map[included_id] = settlement

    statuses = []
    for suborder in delivered_suborders:
        settlement = settlement_map.get(suborder.id)
        if settlement is None:
            statuses.append("Pending record")
        elif settlement.status == Settlement.Status.PAID:
            statuses.append("Paid")
        else:
            statuses.append("Generated")

    unique_statuses = sorted(set(statuses))
    return ", ".join(unique_statuses)


def build_recent_finance_activity(suborders, *, limit=6):
    activity = []
    for suborder in suborders:
        activity.append(
            {
                "kind": "suborder",
                "title": f"Order #{suborder.order_id} for {suborder.producer.business_name or suborder.producer.username}",
                "timestamp": suborder.delivery_date,
                "detail": f"{suborder.get_status_display()} • payout {suborder.producer_payout_amount:.2f}",
                "amount": suborder.commission_amount,
                "order_id": suborder.order_id,
            }
        )

    settlement_qs = Settlement.objects.select_related("producer").order_by("-generated_at", "-id")[:limit]
    for settlement in settlement_qs:
        activity.append(
            {
                "kind": "settlement",
                "title": f"Settlement for {settlement.producer.business_name or settlement.producer.username}",
                "timestamp": settlement.paid_at or settlement.generated_at,
                "detail": settlement.get_status_display(),
                "amount": settlement.payout_amount,
                "settlement_id": settlement.id,
            }
        )

    activity.sort(key=lambda item: item["timestamp"], reverse=True)
    return activity[:limit]


def request_external_payout_api(settlement):
    """
    Send the payout instruction to the external payments service over HTTP.

    The external service returns a real API response and provider reference,
    but this prototype endpoint does not move real money.
    """
    if settlement.payout_amount <= Decimal("0.00"):
        return PayoutApiResult(
            success=False,
            message="The payout service rejected a zero-value payout.",
        )

    endpoint = f"{settings.PAYMENTS_SERVICE_URL.rstrip('/')}/api/payouts/send"
    payload = {
        "settlement_id": settlement.id,
        "producer_id": settlement.producer_id,
        "producer_name": settlement.producer.business_name or settlement.producer.username,
        "week_start": settlement.week_start.isoformat(),
        "week_end": settlement.week_end.isoformat(),
        "amount": str(settlement.payout_amount),
        "currency": "GBP",
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urlopen(request, timeout=5) as response:
            parsed = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return PayoutApiResult(
            success=False,
            message=f"External payout API unavailable: {exc}",
        )

    return PayoutApiResult(
        success=bool(parsed.get("success")),
        reference=str(parsed.get("reference") or ""),
        message=str(parsed.get("message") or ""),
    )


def send_settlement_payout(settlement):
    """
    Send a settlement payout through the external payout API.
    """
    if settlement.status == Settlement.Status.PAID and settlement.payout_receipt_reference:
        raise ValidationError("This settlement has already been paid.")

    result = request_external_payout_api(settlement)
    settlement.payout_provider = PAYOUT_API_PROVIDER
    settlement.payout_requested_at = timezone.now()

    if result.success:
        settlement.status = Settlement.Status.PAID
        settlement.paid_at = settlement.payout_requested_at
        settlement.payout_reference = result.reference
        settlement.payout_error = ""
    else:
        settlement.status = Settlement.Status.FAILED
        settlement.paid_at = None
        settlement.payout_reference = ""
        settlement.payout_error = result.message

    settlement.save(
        update_fields=[
            "status",
            "paid_at",
            "payout_provider",
            "payout_reference",
            "payout_error",
            "payout_requested_at",
        ]
    )
    return result


def build_producer_finance_summaries(suborders):
    grouped = {}
    for suborder in suborders:
        summary = grouped.setdefault(
            suborder.producer_id,
            {
                "producer": suborder.producer,
                "gross_sales": Decimal("0.00"),
                "commission_total": Decimal("0.00"),
                "payout_total": Decimal("0.00"),
                "suborder_count": 0,
                "order_ids": set(),
                "latest_delivery": suborder.delivery_date,
            },
        )
        summary["gross_sales"] += suborder.subtotal
        summary["commission_total"] += suborder.commission_amount
        summary["payout_total"] += suborder.producer_payout_amount
        summary["suborder_count"] += 1
        summary["order_ids"].add(suborder.order_id)
        if suborder.delivery_date > summary["latest_delivery"]:
            summary["latest_delivery"] = suborder.delivery_date

    summaries = list(grouped.values())
    for summary in summaries:
        summary["order_count"] = len(summary["order_ids"])
    return sorted(
        summaries,
        key=lambda summary: (
            summary["commission_total"],
            (summary["producer"].business_name or summary["producer"].username).lower(),
        ),
        reverse=True,
    )


def find_payment_snapshot_for_market_order(order):
    """
    Match a marketplace order to the stored payment snapshot for admin tracing.
    """
    market_signature = sorted(
        (item.product_name, str(item.unit_price), item.quantity)
        for suborder in order.producer_suborders.prefetch_related("items").all()
        for item in suborder.items.all()
    )

    candidates = list(
        PaymentSnapshot.objects.select_related("order")
        .prefetch_related("order__items")
        .filter(
            order__user=order.customer,
            subtotal=order.total_amount,
            commission_amount=order.commission_total,
        )
        .order_by("-created_at", "-id")
    )
    if not candidates:
        return None

    if market_signature:
        exact_matches = []
        for payment in candidates:
            payment_signature = sorted(
                (item.product_name, str(item.unit_price), item.quantity)
                for item in payment.order.items.all()
            )
            if payment_signature == market_signature:
                exact_matches.append(payment)
        if exact_matches:
            return min(
                exact_matches,
                key=lambda payment: abs((payment.order.created_at - order.created_at).total_seconds()),
            )

    close_matches = [
        payment for payment in candidates
        if order.created_at - timedelta(minutes=10) <= payment.order.created_at <= order.created_at + timedelta(minutes=10)
    ]
    if close_matches:
        return min(
            close_matches,
            key=lambda payment: abs((payment.order.created_at - order.created_at).total_seconds()),
        )

    return min(
        candidates,
        key=lambda payment: abs((payment.order.created_at - order.created_at).total_seconds()),
    )


def calculate_running_period_summaries(reference=None):
    """
    Return explicit current-month and year-to-date totals for finance reporting.
    """
    reference = timezone.localtime(reference or timezone.now())
    delivered = base_finance_queryset().filter(status=ProducerSubOrder.Status.DELIVERED)

    month_start = reference.replace(day=1, hour=0, minute=0, second=0, microsecond=0).date()
    ytd_start = reference.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0).date()

    month_totals = aggregate_finance_totals(delivered.filter(delivery_date__date__gte=month_start))
    ytd_totals = aggregate_finance_totals(delivered.filter(delivery_date__date__gte=ytd_start))

    return {
        "month": {
            "label": reference.strftime("%B %Y"),
            **month_totals,
        },
        "ytd": {
            "label": f"{reference.year} year to date",
            **ytd_totals,
        },
    }


def build_admin_report_rows(suborders):
    """
    Flatten suborder finance rows for export in audit-friendly formats.
    """
    rows = []
    for suborder in suborders:
        rows.append(
            {
                "suborder_id": suborder.id,
                "order_id": suborder.order.id,
                "producer": suborder.producer.business_name or suborder.producer.username,
                "customer": suborder.order.customer.username,
                "delivery_date": timezone.localtime(suborder.delivery_date).strftime("%Y-%m-%d %H:%M"),
                "status": suborder.status,
                "order_total": suborder.order.total_amount,
                "order_commission_total": suborder.order.commission_total,
                "subtotal": suborder.subtotal,
                "commission_amount": suborder.commission_amount,
                "producer_payout_amount": suborder.producer_payout_amount,
            }
        )
    return rows


def build_finance_pdf(lines):
    """
    Generate a lightweight PDF document without external dependencies.

    This keeps reporting self-contained while still offering a downloadable PDF.
    """
    def escape_pdf_text(value):
        return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def wrap_line(value, width=92):
        words = str(value).split()
        if not words:
            return [""]

        wrapped = []
        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            if len(candidate) <= width:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)
        return wrapped

    wrapped_lines = []
    for line in lines:
        wrapped_lines.extend(wrap_line(line))

    page_height = 842
    top_margin = 800
    bottom_margin = 48
    leading = 16
    lines_per_page = max(1, int((top_margin - bottom_margin) / leading))
    line_chunks = [
        wrapped_lines[index:index + lines_per_page]
        for index in range(0, len(wrapped_lines), lines_per_page)
    ] or [[""]]

    objects = [
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
    ]

    page_ids = []
    content_ids = []
    font_id = 3 + (2 * len(line_chunks))
    next_object_id = 3

    for page_lines in line_chunks:
        page_id = next_object_id
        content_id = next_object_id + 1
        next_object_id += 2
        page_ids.append(page_id)
        content_ids.append(content_id)

        content_lines = ["BT", "/F1 11 Tf", f"{leading} TL", f"40 {top_margin} Td"]
        for index, line in enumerate(page_lines):
            if index > 0:
                content_lines.append("T*")
            content_lines.append(f"({escape_pdf_text(line)}) Tj")
        content_lines.append("ET")
        content = "\n".join(content_lines).encode("latin-1", "replace")

        objects.append(
            f"{page_id} 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 {page_height}] /Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >> endobj".encode("ascii")
        )
        objects.append(
            f"{content_id} 0 obj << /Length {len(content)} >> stream\n".encode("ascii") + content + b"\nendstream endobj"
        )

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects.insert(1, f"2 0 obj << /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >> endobj".encode("ascii"))
    objects.append(f"{font_id} 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj".encode("ascii"))

    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj in objects:
        offsets.append(len(pdf))
        pdf.extend(obj)
        pdf.extend(b"\n")

    xref_offset = len(pdf)
    pdf.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer << /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF".encode("ascii")
    )
    return bytes(pdf)


def settlement_week_bounds(delivery_dt):
    local_delivery = timezone.localtime(delivery_dt)
    week_start = (local_delivery - timedelta(days=local_delivery.weekday())).date()
    week_end = week_start + timedelta(days=6)
    return week_start, week_end


def build_settlement_summaries(suborders):
    settlement_candidates = [suborder for suborder in suborders if suborder.status == ProducerSubOrder.Status.DELIVERED]
    settlement_groups = defaultdict(
        lambda: {
            "producer": None,
            "week_start": None,
            "week_end": None,
            "gross_sales": Decimal("0.00"),
            "commission": Decimal("0.00"),
            "payout": Decimal("0.00"),
            "suborder_count": 0,
            "suborders": [],
        }
    )

    for suborder in settlement_candidates:
        week_start, week_end = settlement_week_bounds(suborder.delivery_date)
        key = (suborder.producer_id, week_start)
        group = settlement_groups[key]
        group["producer"] = suborder.producer
        group["week_start"] = week_start
        group["week_end"] = week_end
        group["gross_sales"] += suborder.subtotal
        group["commission"] += suborder.commission_amount
        group["payout"] += suborder.producer_payout_amount
        group["suborder_count"] += 1
        group["suborders"].append(suborder)

    return sorted(
        settlement_groups.values(),
        key=lambda settlement: (settlement["week_start"], settlement["producer"].username.lower()),
        reverse=True,
    )


def get_settlement_suborders(*, producer_id=None, week_start=None, record=None):
    queryset = (
        base_finance_queryset()
        .filter(status=ProducerSubOrder.Status.DELIVERED)
        .prefetch_related("settlements")
    )

    current_record_id = None
    if record is not None:
        current_record_id = record.id
        producer_id = record.producer_id
        week_start = record.week_start

    if producer_id is not None:
        queryset = queryset.filter(producer_id=producer_id)

    if week_start is not None:
        queryset = queryset.filter(delivery_date__date__gte=week_start, delivery_date__date__lte=week_start + timedelta(days=6))

    eligible = []
    for suborder in queryset:
        computed_week_start, _ = settlement_week_bounds(suborder.delivery_date)
        if week_start is not None and computed_week_start != week_start:
            continue

        other_settlement_ids = [
            settlement.id
            for settlement in suborder.settlements.all()
            if settlement.id != current_record_id
        ]
        if other_settlement_ids:
            continue
        eligible.append(suborder)
    return eligible


def _settlement_row_matches_query(row, query):
    query = (query or "").strip().lower()
    if not query:
        return True

    producer = row["producer"]
    searchable_values = [
        getattr(producer, "username", ""),
        getattr(producer, "email", ""),
        getattr(producer, "business_name", ""),
        str(row["week_start"]),
        str(row["week_end"]),
    ]
    record = row.get("record")
    if record is not None:
        searchable_values.extend([str(record.id), record.payout_reference])

    for suborder in row.get("suborders", []):
        searchable_values.extend(
            [
                str(suborder.id),
                str(suborder.order_id),
                getattr(suborder.order.customer, "username", ""),
                getattr(suborder.order.customer, "email", ""),
                getattr(suborder.producer, "username", ""),
                getattr(suborder.producer, "business_name", ""),
            ]
        )
        searchable_values.extend(item.product_name for item in suborder.items.all())

    return any(query in str(value).lower() for value in searchable_values if value)


def build_settlement_dashboard_rows(suborders, *, producer=None, settlement_week="", q=""):
    candidate_summaries = build_settlement_summaries(suborders)
    if producer is not None:
        settlement_records = Settlement.objects.filter(producer=producer)
    else:
        settlement_records = Settlement.objects.all()

    settlement_records = settlement_records.select_related("producer").order_by("-week_start", "producer__username")
    if settlement_week:
        settlement_records = settlement_records.filter(week_start=settlement_week)

    candidate_map = {
        (summary["producer"].id, summary["week_start"]): summary
        for summary in candidate_summaries
    }

    rows = []
    for record in settlement_records:
        record_suborders = list(
            record.included_suborders.select_related("order", "order__customer", "producer").prefetch_related("items")
        )
        summary = candidate_map.pop((record.producer_id, record.week_start), None)
        rows.append(
            {
                "producer": record.producer,
                "week_start": record.week_start,
                "week_end": record.week_end,
                "gross_sales": record.gross_sales,
                "commission": record.commission_amount,
                "payout": record.payout_amount,
                "suborder_count": record.suborder_count,
                "suborders": record_suborders,
                "has_data": bool(record_suborders) or record.suborder_count > 0,
                "record": record,
                "is_pending": False,
            }
        )

    for summary in candidate_map.values():
        if settlement_week and summary["week_start"].isoformat() != settlement_week:
            continue
        rows.append(
            {
                **summary,
                "has_data": True,
                "record": None,
                "is_pending": True,
            }
        )

    if q:
        rows = [row for row in rows if _settlement_row_matches_query(row, q)]

    return sorted(
        rows,
        key=lambda settlement: (
            settlement["week_start"],
            (getattr(settlement["producer"], "business_name", "") or settlement["producer"].username).lower(),
        ),
        reverse=True,
    )


def generate_weekly_settlements(*, actor, producer=None, week_start=None):
    """
    Persist settlement records from delivered producer suborders.
    """
    if actor is None:
        raise ValidationError("An actor is required to generate settlements.")

    settlements = []
    producers = [producer] if producer is not None else None
    if producers is None:
        producer_ids = (
            base_finance_queryset()
            .filter(status=ProducerSubOrder.Status.DELIVERED)
            .values_list("producer_id", flat=True)
            .distinct()
        )
        producer_map = {
            suborder.producer_id: suborder.producer
            for suborder in base_finance_queryset()
            .filter(status=ProducerSubOrder.Status.DELIVERED, producer_id__in=producer_ids)
            .select_related("producer")
        }
        producers = [producer_map[producer_id] for producer_id in producer_ids if producer_id in producer_map]

    for producer_obj in producers:
        if week_start is not None:
            week_starts = [week_start]
        else:
            week_starts = sorted(
                {
                    settlement_week_bounds(suborder.delivery_date)[0]
                    for suborder in get_settlement_suborders(producer_id=producer_obj.id)
                },
                reverse=True,
            )

        for summary_week_start in week_starts:
            settlement, _ = Settlement.objects.get_or_create(
                producer=producer_obj,
                week_start=summary_week_start,
                defaults={
                    "week_end": summary_week_start + timedelta(days=6),
                    "generated_by": actor,
                },
            )
            suborders = get_settlement_suborders(record=settlement)
            if not suborders:
                continue

            summary = {
                "week_end": summary_week_start + timedelta(days=6),
                "gross_sales": sum((suborder.subtotal for suborder in suborders), Decimal("0.00")),
                "commission": sum((suborder.commission_amount for suborder in suborders), Decimal("0.00")),
                "payout": sum((suborder.producer_payout_amount for suborder in suborders), Decimal("0.00")),
                "suborder_count": len(suborders),
            }
            settlement.week_end = summary["week_end"]
            settlement.gross_sales = summary["gross_sales"]
            settlement.commission_amount = summary["commission"]
            settlement.payout_amount = summary["payout"]
            settlement.suborder_count = summary["suborder_count"]
            settlement.generated_by = actor
            valid_paid_record = settlement.status == Settlement.Status.PAID and settlement.payout_receipt_reference
            if not valid_paid_record:
                settlement.status = Settlement.Status.GENERATED
                settlement.paid_at = None
                settlement.payout_provider = ""
                settlement.payout_reference = ""
                settlement.payout_error = ""
                settlement.payout_requested_at = None
            settlement.save()
            settlement.included_suborders.set(suborders)
            settlements.append(settlement)
    return settlements


def build_recurring_template_from_order(order):
    """
    Capture an order as a reusable recurring template without changing the source order.
    """
    items = []
    delivery_time_counter = Counter()
    for suborder in order.producer_suborders.prefetch_related("items").all():
        local_delivery = timezone.localtime(suborder.delivery_date)
        for item in suborder.items.all():
            delivery_label = local_delivery.strftime("%H:%M")
            delivery_time_counter[delivery_label] += item.quantity
            items.append(
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "delivery_time": delivery_label,
                }
            )

    return {
        "delivery_address": order.delivery_address,
        "customer_phone": order.customer_phone,
        "special_instructions": order.special_instructions,
        "preferred_delivery_time": delivery_time_counter.most_common(1)[0][0] if delivery_time_counter else "12:00",
        "items": items,
    }


def _coerce_template_items(raw_items):
    if not isinstance(raw_items, list) or not raw_items:
        raise ValidationError("Recurring orders must contain at least one item.")

    normalized = []
    for index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, dict):
            raise ValidationError(f"Recurring order item {index} must be a JSON object.")

        try:
            product_id = int(raw_item["product_id"])
            quantity = int(raw_item["quantity"])
        except (KeyError, TypeError, ValueError):
            raise ValidationError(
                f"Recurring order item {index} must include valid product_id and quantity."
            )

        if quantity <= 0:
            raise ValidationError(f"Recurring order item {index} must have quantity greater than zero.")

        normalized.append(
            {
                "product_id": product_id,
                "quantity": quantity,
                "delivery_time": (raw_item.get("delivery_time") or "12:00").strip(),
            }
        )

    return normalized


def validate_recurring_order_template(template_order_data):
    """
    Validate recurring order JSON and confirm stock availability before generation.
    """
    if not isinstance(template_order_data, dict):
        raise ValidationError("Template order data must be a JSON object.")

    items = _coerce_template_items(template_order_data.get("items", []))
    products = Product.objects.select_related("producer").filter(
        id__in=[item["product_id"] for item in items]
    )
    product_map = {product.id: product for product in products}

    for item in items:
        product = product_map.get(item["product_id"])
        if product is None:
            raise ValidationError(f"Product {item['product_id']} does not exist.")
        if not product.is_active:
            raise ValidationError(f"{product.name} is no longer available.")
        if product.stock_quantity < item["quantity"]:
            raise ValidationError(
                f"Not enough stock for {product.name}. Available: {product.stock_quantity}."
            )

    return items, product_map


def update_next_instance_overrides(recurring_order, overrides):
    """
    Save one-off changes for the next generated order while leaving the template untouched.
    """
    if overrides is None:
        overrides = {}
    if not isinstance(overrides, dict):
        raise ValidationError("Next instance overrides must be a JSON object.")

    merged = deepcopy(recurring_order.next_instance_overrides or {})
    merged.update(overrides)
    recurring_order.next_instance_overrides = merged
    recurring_order.full_clean()
    recurring_order.save(update_fields=["next_instance_overrides", "updated_at"])
    return recurring_order


def update_recurring_order_status(recurring_order, status):
    if status not in RecurringOrder.Status.values:
        raise ValidationError("Invalid recurring order status.")
    recurring_order.status = status
    recurring_order.active = status == RecurringOrder.Status.ACTIVE
    recurring_order.full_clean()
    recurring_order.save(update_fields=["status", "active", "updated_at"])
    return recurring_order


def update_recurring_order_delivery_schedule(recurring_order, delivery_date_value, delivery_time_value):
    try:
        parsed_date = date.fromisoformat(delivery_date_value or "")
    except ValueError as exc:
        raise ValidationError("Please enter a valid delivery date.") from exc

    try:
        parsed_time = datetime.strptime(delivery_time_value or "", "%H:%M").time()
    except ValueError as exc:
        raise ValidationError("Please enter a valid delivery time.") from exc

    requested_delivery = timezone.make_aware(
        datetime.combine(parsed_date, parsed_time),
        timezone.get_current_timezone(),
    )
    earliest_delivery = timezone.now() + timedelta(hours=48)
    if requested_delivery < earliest_delivery:
        raise ValidationError("Please choose a delivery slot at least 48 hours from now.")

    template = deepcopy(recurring_order.template_order_data or {})
    template["preferred_delivery_time"] = parsed_time.strftime("%H:%M")
    items = template.get("items", [])
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict):
                item["delivery_time"] = parsed_time.strftime("%H:%M")

    recurring_order.next_run_date = parsed_date
    recurring_order.preferred_delivery_time = parsed_time
    recurring_order.template_order_data = template
    recurring_order.full_clean()
    recurring_order.save(update_fields=["next_run_date", "preferred_delivery_time", "template_order_data", "updated_at"])
    return recurring_order


def update_recurring_order_details(recurring_order, *, delivery_address, customer_phone, special_instructions, quantities):
    template = deepcopy(recurring_order.template_order_data or {})
    template["delivery_address"] = (delivery_address or "").strip()
    template["customer_phone"] = (customer_phone or "").strip()
    template["special_instructions"] = (special_instructions or "").strip()

    normalized_quantities = {}
    for product_id, raw_quantity in (quantities or {}).items():
        try:
            quantity = int(raw_quantity)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Please enter valid quantities.") from exc
        if quantity <= 0:
            raise ValidationError("Quantities must be at least 1.")
        normalized_quantities[int(product_id)] = quantity

    items = template.get("items", [])
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = item.get("product_id")
            if product_id in normalized_quantities:
                item["quantity"] = normalized_quantities[product_id]

    recurring_order.template_order_data = template
    recurring_order.next_instance_overrides = {}
    recurring_order.full_clean()
    recurring_order.save(update_fields=["template_order_data", "next_instance_overrides", "updated_at"])
    return recurring_order


def _build_delivery_datetime(next_run_date, delivery_time_value):
    try:
        parsed_time = datetime.strptime(delivery_time_value or "12:00", "%H:%M").time()
    except ValueError as exc:
        raise ValidationError("Delivery times must use HH:MM format.") from exc

    naive_value = datetime.combine(next_run_date, parsed_time)
    return timezone.make_aware(naive_value, timezone.get_current_timezone())


def get_recurring_order_preview(recurring_order):
    payload = deepcopy(recurring_order.template_order_data or {})
    payload.update(recurring_order.next_instance_overrides or {})
    items = payload.get("items", [])
    preview_items = []
    issues = []
    if not isinstance(items, list):
        return {"items": [], "issues": ["Template items are invalid."]}

    product_ids = []
    for raw_item in items:
        if isinstance(raw_item, dict) and raw_item.get("product_id"):
            product_ids.append(raw_item["product_id"])
    product_map = {
        product.id: product
        for product in Product.objects.select_related("producer").filter(id__in=product_ids)
    }

    for index, raw_item in enumerate(items, start=1):
        if not isinstance(raw_item, dict):
            issues.append(f"Item {index} is invalid.")
            continue

        product = product_map.get(raw_item.get("product_id"))
        quantity = raw_item.get("quantity") or 0
        delivery_time = raw_item.get("delivery_time") or payload.get("preferred_delivery_time") or "12:00"
        unit_price = product.price if product else Decimal("0.00")
        line_total = (unit_price * quantity).quantize(Decimal("0.01"))
        preview_items.append(
            {
                "product": product,
                "quantity": quantity,
                "delivery_time": delivery_time,
                "unit_price": unit_price,
                "line_total": line_total,
                "is_available": bool(
                    product and product.is_active and product.stock_quantity >= quantity and quantity > 0
                ),
            }
        )
        if product is None:
            issues.append(f"Product {raw_item.get('product_id')} no longer exists.")
        elif not product.is_active:
            issues.append(f"{product.name} is no longer available.")
        elif product.stock_quantity < quantity:
            issues.append(f"{product.name} only has {product.stock_quantity} available.")

    subtotal = sum((item["line_total"] for item in preview_items), Decimal("0.00")).quantize(Decimal("0.01"))
    commission = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
    total = (subtotal + commission).quantize(Decimal("0.01"))

    return {
        "items": preview_items,
        "issues": issues,
        "subtotal": subtotal,
        "commission": commission,
        "total": total,
    }


def generate_order_from_recurring(recurring_order):
    """
    Materialize a recurring template as a new multi-vendor market order.
    """
    if recurring_order.status != RecurringOrder.Status.ACTIVE:
        raise ValidationError("This recurring order is not active.")

    payload = deepcopy(recurring_order.template_order_data or {})
    payload.update(recurring_order.next_instance_overrides or {})
    items, product_map = validate_recurring_order_template(payload)

    resolved_items = []
    for item in items:
        resolved_items.append(
            {
                **item,
                "product": product_map[item["product_id"]],
                "delivery_dt": _build_delivery_datetime(
                    recurring_order.next_run_date,
                    item.get("delivery_time", "12:00"),
                ),
            }
        )

    grouped = defaultdict(
        lambda: {
            "producer": None,
            "subtotal": Decimal("0.00"),
            "delivery_date": None,
            "items": [],
        }
    )
    for item in resolved_items:
        product = item["product"]
        group = grouped[product.producer_id]
        group["producer"] = product.producer
        group["items"].append(item)
        group["subtotal"] += (product.price * item["quantity"]).quantize(Decimal("0.01"))
        if group["delivery_date"] is None or item["delivery_dt"] < group["delivery_date"]:
            group["delivery_date"] = item["delivery_dt"]

    total_amount = sum((group["subtotal"] for group in grouped.values()), Decimal("0.00"))
    commission_total = (total_amount * Decimal("0.05")).quantize(Decimal("0.01"))
    total_subtotal = total_amount or Decimal("1.00")
    product_ids = [item["product_id"] for item in resolved_items]

    with transaction.atomic():
        products_for_update = {
            product.id: product
            for product in Product.objects.select_for_update().filter(id__in=product_ids)
        }
        order = Order.objects.create(
            customer=recurring_order.customer,
            status=Order.Status.CREATED,
            total_amount=total_amount,
            commission_total=commission_total,
            delivery_address=payload.get("delivery_address", ""),
            customer_phone=payload.get("customer_phone", ""),
            special_instructions=payload.get("special_instructions", ""),
        )

        remaining_commission = commission_total
        producer_ids = list(grouped.keys())
        for producer_id in producer_ids:
            group = grouped[producer_id]
            subtotal = group["subtotal"].quantize(Decimal("0.01"))
            if producer_id == producer_ids[-1]:
                commission_amount = remaining_commission
            else:
                commission_amount = (commission_total * subtotal / total_subtotal).quantize(Decimal("0.01"))
                remaining_commission -= commission_amount

            group["suborder"] = ProducerSubOrder.objects.create(
                order=order,
                producer=group["producer"],
                status=ProducerSubOrder.Status.PENDING,
                delivery_date=group["delivery_date"],
                subtotal=subtotal,
                commission_amount=commission_amount,
                producer_payout_amount=(subtotal - commission_amount).quantize(Decimal("0.01")),
            )

        for item in resolved_items:
            product = products_for_update[item["product_id"]]
            if product.stock_quantity < item["quantity"]:
                raise ValidationError(
                    f"Not enough stock for {product.name}. Available: {product.stock_quantity}."
                )

            OrderItem.objects.create(
                suborder=grouped[product.producer_id]["suborder"],
                product=product,
                product_name=product.name,
                unit_price=product.price,
                quantity=item["quantity"],
            )
            product.stock_quantity -= item["quantity"]
            if product.stock_quantity == 0:
                product.is_active = False
            product.save(update_fields=["stock_quantity", "is_active"])

    recurring_order.last_run_at = timezone.now()
    recurring_order.next_run_date = recurring_order.next_run_date + timedelta(days=7)
    recurring_order.next_instance_overrides = {}
    recurring_order.save(update_fields=["last_run_at", "next_run_date", "next_instance_overrides", "updated_at"])
    return order


def generate_due_recurring_orders(*, run_date=None):
    if isinstance(run_date, str) and run_date:
        run_date = date.fromisoformat(run_date)
    run_date = run_date or timezone.localdate()
    generated_orders = []
    recurring_orders = (
        RecurringOrder.objects.filter(status=RecurringOrder.Status.ACTIVE, next_run_date__lte=run_date)
        .order_by("next_run_date", "id")
    )
    for recurring_order in recurring_orders:
        generated_orders.append(generate_order_from_recurring(recurring_order))
    return generated_orders
