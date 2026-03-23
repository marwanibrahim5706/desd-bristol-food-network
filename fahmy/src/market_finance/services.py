from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from market_orders.models import Order, OrderItem, ProducerSubOrder
from market_payments.models import Settlement
from market_products.models import Product

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


def generate_weekly_settlements(*, actor, producer=None, week_start=None):
    """
    Persist settlement records from delivered producer suborders.
    """
    if actor is None:
        raise ValidationError("An actor is required to generate settlements.")

    queryset = base_finance_queryset().filter(status=ProducerSubOrder.Status.DELIVERED)
    if producer is not None:
        queryset = queryset.filter(producer=producer)

    settlements = []
    for summary in build_settlement_summaries(queryset):
        if week_start and summary["week_start"] != week_start:
            continue

        settlement, _ = Settlement.objects.update_or_create(
            producer=summary["producer"],
            week_start=summary["week_start"],
            defaults={
                "week_end": summary["week_end"],
                "gross_sales": summary["gross_sales"],
                "commission_amount": summary["commission"],
                "payout_amount": summary["payout"],
                "suborder_count": summary["suborder_count"],
                "status": Settlement.Status.GENERATED,
                "generated_by": actor,
                "paid_at": None,
            },
        )
        settlements.append(settlement)
    return settlements


def build_recurring_template_from_order(order):
    """
    Capture an order as a reusable recurring template without changing the source order.
    """
    items = []
    for suborder in order.producer_suborders.prefetch_related("items").all():
        local_delivery = timezone.localtime(suborder.delivery_date)
        for item in suborder.items.all():
            items.append(
                {
                    "product_id": item.product_id,
                    "quantity": item.quantity,
                    "delivery_time": local_delivery.strftime("%H:%M"),
                }
            )

    return {
        "delivery_address": order.delivery_address,
        "customer_phone": order.customer_phone,
        "special_instructions": order.special_instructions,
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


def _build_delivery_datetime(next_run_date, delivery_time_value):
    try:
        parsed_time = datetime.strptime(delivery_time_value or "12:00", "%H:%M").time()
    except ValueError as exc:
        raise ValidationError("Delivery times must use HH:MM format.") from exc

    naive_value = datetime.combine(next_run_date, parsed_time)
    return timezone.make_aware(naive_value, timezone.get_current_timezone())


def generate_order_from_recurring(recurring_order):
    """
    Materialize a recurring template as a new multi-vendor market order.
    """
    if not recurring_order.active:
        raise ValidationError("This recurring order is inactive.")

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
