import csv
import json
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import is_admin, is_producer
from market_orders.models import Order, ProducerSubOrder
from market_payments.models import Settlement
from .models import RecurringOrder
from .services import (
    aggregate_finance_totals,
    apply_finance_filters,
    base_finance_queryset,
    build_admin_report_rows,
    build_producer_finance_summaries,
    build_finance_pdf,
    build_order_finance_summaries,
    build_recent_finance_activity,
    build_recurring_template_from_order,
    build_settlement_dashboard_rows,
    calculate_running_period_summaries,
    count_processed_orders,
    find_payment_snapshot_for_market_order,
    generate_order_from_recurring,
    generate_weekly_settlements,
    get_recurring_order_preview,
    get_settlement_suborders,
    send_settlement_payout,
    settlement_week_bounds,
    update_recurring_order_delivery_schedule,
    update_recurring_order_details,
    update_recurring_order_status,
    update_next_instance_overrides,
)


ADMIN_PERIOD_PRESETS = {
    "7d": {"label": "Last 7 days", "days": 7},
    "14d": {"label": "Previous 2 weeks", "days": 14},
    "month": {"label": "This month", "mode": "month"},
    "ytd": {"label": "Year to date", "mode": "ytd"},
    "custom": {"label": "Custom date range", "mode": "custom"},
}

PAYMENT_CARD_TYPE_LABELS = {
    "visa_debit": "Visa Debit",
    "visa_credit": "Visa Credit",
    "mastercard_debit": "Mastercard Debit",
    "mastercard_credit": "Mastercard Credit",
    "amex": "American Express",
    "maestro": "Maestro",
    "card": "Visa Debit",
    "demo_card": "Visa Debit",
}


def _settlement_week_navigation(all_settlements, selected_week):
    available_weeks = []
    seen_weeks = set()
    for settlement in all_settlements:
        week_value = settlement["week_start"].isoformat()
        if week_value in seen_weeks:
            continue
        seen_weeks.add(week_value)
        available_weeks.append(
            {
                "value": week_value,
                "label": f"{settlement['week_start'].strftime('%d %b')} - {settlement['week_end'].strftime('%d %b %Y')}",
            }
        )
    available_weeks = sorted(available_weeks, key=lambda option: option["value"])
    week_values = [option["value"] for option in available_weeks]

    previous_week = ""
    next_week = ""
    if selected_week in week_values:
        idx = week_values.index(selected_week)
        if idx > 0:
            previous_week = week_values[idx - 1]
        if idx < len(week_values) - 1:
            next_week = week_values[idx + 1]

    return available_weeks, previous_week, next_week


def _querystring_with_week(request, week_value):
    if not week_value:
        return ""
    params = request.GET.copy()
    params["settlement_week"] = week_value
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""


def _month_start(date_value):
    return date_value.replace(day=1)


def _month_end(date_value):
    return date_value.replace(day=calendar.monthrange(date_value.year, date_value.month)[1])


def _split_settlement_gap(range_start, range_end, *, preserve_full_gap=False):
    if range_start > range_end:
        return []
    if preserve_full_gap:
        return [(range_start, range_end)]

    periods = []
    cursor = range_start
    while cursor <= range_end:
        period_end = min(cursor + timedelta(days=6), range_end)
        periods.append((cursor, period_end))
        cursor = period_end + timedelta(days=1)
    return periods


def _resolve_admin_period_filters(request):
    today = timezone.localdate()
    period = (request.GET.get("period") or "14d").strip().lower()
    if period not in ADMIN_PERIOD_PRESETS:
        period = "14d"

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()

    if period != "custom":
        preset = ADMIN_PERIOD_PRESETS[period]
        if preset.get("days"):
            start = today - timedelta(days=preset["days"] - 1)
            date_from = start.isoformat()
            date_to = today.isoformat()
        elif preset["mode"] == "month":
            date_from = today.replace(day=1).isoformat()
            date_to = today.isoformat()
        elif preset["mode"] == "ytd":
            date_from = today.replace(month=1, day=1).isoformat()
            date_to = today.isoformat()

    return period, date_from, date_to


def _serialize_report_filters(*, status_filter, producer_filter, q, date_from, date_to, period, settlement_week=""):
    return {
        "status": status_filter,
        "producer": producer_filter,
        "q": q,
        "date_from": date_from,
        "date_to": date_to,
        "period": period,
        "settlement_week": settlement_week,
    }


def _build_querystring(params, exclude=None):
    exclude = set(exclude or [])
    filtered = {key: value for key, value in params.items() if value and key not in exclude}
    if not filtered:
        return ""
    return f"?{urlencode(filtered)}"


def _build_sent_payouts(*, producer_filter="", q="", date_from="", date_to=""):
    payouts = Settlement.objects.select_related("producer").filter(
        status=Settlement.Status.PAID,
        payout_provider="external_payout_api",
    ).exclude(payout_reference="").exclude(payout_reference__startswith="DEMO-")
    if producer_filter:
        payouts = payouts.filter(producer_id=producer_filter)
    if q:
        payouts = payouts.filter(
            Q(producer__username__icontains=q)
            | Q(producer__business_name__icontains=q)
            | Q(payout_reference__icontains=q)
        )
    if date_from:
        payouts = payouts.filter(paid_at__date__gte=date_from)
    if date_to:
        payouts = payouts.filter(paid_at__date__lte=date_to)
    return list(payouts.order_by("-paid_at", "-id"))


def _build_admin_finance_context(request, *, current_section):
    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    status_filter = (request.GET.get("status") or "").strip()
    if current_section == "settlements":
        status_filter = ""
    q = (request.GET.get("q") or "").strip()
    producer_filter = (request.GET.get("producer") or "").strip()
    period, date_from, date_to = _resolve_admin_period_filters(request)
    settlement_week = (request.GET.get("settlement_week") or "").strip()
    report_filters = _serialize_report_filters(
        status_filter=status_filter,
        producer_filter=producer_filter,
        q=q,
        date_from=date_from,
        date_to=date_to,
        period=period,
        settlement_week=settlement_week,
    )

    suborders_qs = apply_finance_filters(
        base_finance_queryset(),
        status_filter=status_filter,
        producer_filter=producer_filter,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    if not status_filter:
        suborders_qs = suborders_qs.filter(status=ProducerSubOrder.Status.DELIVERED)

    suborders = list(suborders_qs)
    order_summaries = build_order_finance_summaries(suborders)
    producer_summaries = build_producer_finance_summaries(suborders)
    aggregates = aggregate_finance_totals(suborders_qs)
    order_count = len({suborder.order_id for suborder in suborders})
    processed_order_count = count_processed_orders(suborders)
    recent_activity = build_recent_finance_activity(suborders)
    settlement_source_qs = base_finance_queryset().filter(status=ProducerSubOrder.Status.DELIVERED)
    if producer_filter:
        settlement_source_qs = settlement_source_qs.filter(producer_id=producer_filter)
    if current_section != "settlements" and date_from:
        settlement_source_qs = settlement_source_qs.filter(delivery_date__date__gte=date_from)
    if current_section != "settlements" and date_to:
        settlement_source_qs = settlement_source_qs.filter(delivery_date__date__lte=date_to)
    producers = list(
        ProducerSubOrder.objects.select_related("producer")
        .order_by("producer__username")
        .values("producer_id", "producer__username", "producer__business_name")
        .distinct()
    )
    producer_users = []
    seen_producer_ids = set()
    for row in producers:
        producer_obj = getattr(settlement_source_qs.filter(producer_id=row["producer_id"]).first(), "producer", None)
        if producer_obj and producer_obj.id not in seen_producer_ids:
            seen_producer_ids.add(producer_obj.id)
            producer_users.append(producer_obj)

    running_summaries = calculate_running_period_summaries()
    settlement_source = list(settlement_source_qs)
    all_settlement_weeks = build_settlement_dashboard_rows(settlement_source)
    settlements = build_settlement_dashboard_rows(
        settlement_source,
        settlement_week=settlement_week,
    )

    available_weeks, previous_week, next_week = _settlement_week_navigation(all_settlement_weeks, settlement_week)

    paid_settlement_count = Settlement.objects.count()
    paid_producer_count = Settlement.objects.values("producer_id").distinct().count()
    sent_payouts = _build_sent_payouts(
        producer_filter=producer_filter,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )
    sent_payout_total = sum((payout.payout_amount for payout in sent_payouts), Decimal("0.00"))

    return {
        "suborders": suborders,
        "settlements": settlements,
        "status_filter": status_filter,
        "q": q,
        "producer_filter": producer_filter,
        "date_from": date_from,
        "date_to": date_to,
        "settlement_week": settlement_week,
        "status_choices": ProducerSubOrder.Status.choices,
        "producers": producers,
        "gross_sales": aggregates["gross_sales"],
        "total_commission": aggregates["total_commission"],
        "total_payouts": aggregates["total_payouts"],
        "order_count": order_count,
        "processed_order_count": processed_order_count,
        "paid_settlement_count": paid_settlement_count,
        "paid_producer_count": paid_producer_count,
        "sent_payouts": sent_payouts,
        "sent_payout_total": sent_payout_total,
        "available_weeks": available_weeks,
        "previous_week_query": _querystring_with_week(request, previous_week),
        "next_week_query": _querystring_with_week(request, next_week),
        "order_summaries": order_summaries,
        "producer_summaries": producer_summaries,
        "recent_activity": recent_activity,
        "month_summary": running_summaries["month"],
        "ytd_summary": running_summaries["ytd"],
        "current_section": current_section,
        "period": period,
        "period_choices": [{"value": key, "label": value["label"]} for key, value in ADMIN_PERIOD_PRESETS.items()],
        "report_query": _build_querystring(report_filters, exclude={"settlement_week"}),
        "settlement_query": _build_querystring(report_filters),
        "report_filters": report_filters,
    }


def _render_admin_finance_workspace(request, *, current_section):
    return render(
        request,
        "market_finance/admin_finance_dashboard.html",
        _build_admin_finance_context(request, current_section=current_section),
    )


@login_required
def admin_finance_dashboard(request):
    return _render_admin_finance_workspace(request, current_section="overview")


@login_required
def admin_finance_reports(request):
    return _render_admin_finance_workspace(request, current_section="reports")


@login_required
def admin_finance_settlements(request):
    return _render_admin_finance_workspace(request, current_section="settlements")


@login_required
def admin_finance_payouts(request):
    return _render_admin_finance_workspace(request, current_section="payouts")


@login_required
def admin_finance_exports(request):
    return _render_admin_finance_workspace(request, current_section="exports")


@login_required
def generate_settlement(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    producer_id = (request.POST.get("producer_id") or "").strip()
    week_start = (request.POST.get("week_start") or "").strip()

    if not producer_id or not week_start:
        messages.error(request, "Missing settlement details.")
        return redirect("market_finance:admin_finance_dashboard")

    delivered_suborders = base_finance_queryset().filter(
        producer_id=producer_id,
        status=ProducerSubOrder.Status.DELIVERED,
    )

    matching = [
        suborder for suborder in delivered_suborders
        if settlement_week_bounds(suborder.delivery_date)[0].isoformat() == week_start
    ]
    if not matching:
        messages.error(request, "No delivered suborders found for that producer/week.")
        return redirect("market_finance:admin_finance_dashboard")

    start_date = datetime.fromisoformat(week_start).date()
    producer = matching[0].producer
    existing_ids = set(
        Settlement.objects.filter(producer=producer, week_start=start_date).values_list("id", flat=True)
    )
    settlement = generate_weekly_settlements(
        actor=request.user,
        producer=producer,
        week_start=start_date,
    )[0]
    created = settlement.id not in existing_ids

    if created:
        messages.success(request, f"Settlement record generated for {producer} ({start_date}).")
    else:
        messages.success(request, f"Settlement record refreshed for {producer} ({start_date}).")

    return redirect("market_finance:admin_finance_settlements")


@login_required
def send_settlement_payout_view(request, settlement_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    settlement = get_object_or_404(Settlement, id=settlement_id)
    try:
        result = send_settlement_payout(settlement)
    except ValidationError as exc:
        messages.error(request, str(exc))
        return redirect("market_finance:admin_finance_settlements")

    if result.success:
        messages.success(
            request,
            f"External payout sent for settlement #{settlement.id}. Reference: {result.reference}.",
        )
    else:
        messages.error(
            request,
            f"External payout failed for settlement #{settlement.id}: {result.message}",
        )
    return redirect("market_finance:admin_finance_settlements")


@login_required
def producer_settlements_dashboard(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    settlement_week = (request.GET.get("settlement_week") or "").strip()

    delivered_source_qs = apply_finance_filters(
        base_finance_queryset().filter(producer=request.user),
        status_filter=ProducerSubOrder.Status.DELIVERED,
        date_from=date_from,
        date_to=date_to,
    )
    suborders_qs = delivered_source_qs
    all_settlements = build_settlement_dashboard_rows(
        list(
            delivered_source_qs
        ),
        producer=request.user,
        settlement_week=settlement_week,
    )

    available_weeks, previous_week, next_week = _settlement_week_navigation(all_settlements, settlement_week)
    settlements = all_settlements

    totals = aggregate_finance_totals(suborders_qs)

    return render(
        request,
        "market_finance/producer_settlements.html",
        {
            "settlements": settlements,
            "gross_sales": totals["gross_sales"],
            "total_commission": totals["total_commission"],
            "total_payouts": totals["total_payouts"],
            "date_from": date_from,
            "date_to": date_to,
            "settlement_week": settlement_week,
            "available_weeks": available_weeks,
            "previous_week_query": _querystring_with_week(request, previous_week),
            "next_week_query": _querystring_with_week(request, next_week),
        },
    )


@login_required
def export_admin_finance_csv(request):
    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    suborders_qs = apply_finance_filters(
        base_finance_queryset(),
        status_filter=(request.GET.get("status") or "").strip(),
        producer_filter=(request.GET.get("producer") or "").strip(),
        q=(request.GET.get("q") or "").strip(),
        date_from=(request.GET.get("date_from") or "").strip(),
        date_to=(request.GET.get("date_to") or "").strip(),
    )

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="admin_finance_report.csv"'
    writer = csv.writer(response)
    rows = build_admin_report_rows(list(suborders_qs))
    totals = aggregate_finance_totals(suborders_qs)
    writer.writerow(["report_scope", "admin_finance"])
    writer.writerow(["period", (request.GET.get("period") or "14d").strip() or "14d"])
    writer.writerow(["date_from", (request.GET.get("date_from") or "").strip()])
    writer.writerow(["date_to", (request.GET.get("date_to") or "").strip()])
    writer.writerow(["status_filter", (request.GET.get("status") or "").strip()])
    writer.writerow(["producer_filter", (request.GET.get("producer") or "").strip()])
    writer.writerow(["search", (request.GET.get("q") or "").strip()])
    writer.writerow(["gross_sales", totals["gross_sales"]])
    writer.writerow(["commission_amount", totals["total_commission"]])
    writer.writerow(["producer_payout_amount", totals["total_payouts"]])
    writer.writerow([])
    writer.writerow([
        "suborder_id", "order_id", "producer", "customer", "delivery_date",
        "status", "order_total", "order_commission_total", "subtotal", "commission_amount", "producer_payout_amount",
    ])
    for row in rows:
        writer.writerow([
            row["suborder_id"],
            row["order_id"],
            row["producer"],
            row["customer"],
            row["delivery_date"],
            row["status"],
            row["order_total"],
            row["order_commission_total"],
            row["subtotal"],
            row["commission_amount"],
            row["producer_payout_amount"],
        ])
    return response


@login_required
def export_admin_finance_excel(request):
    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    suborders = list(
        apply_finance_filters(
            base_finance_queryset(),
            status_filter=(request.GET.get("status") or "").strip(),
            producer_filter=(request.GET.get("producer") or "").strip(),
            q=(request.GET.get("q") or "").strip(),
            date_from=(request.GET.get("date_from") or "").strip(),
            date_to=(request.GET.get("date_to") or "").strip(),
        )
    )
    rows = build_admin_report_rows(suborders)
    response = HttpResponse(content_type="application/vnd.ms-excel")
    response["Content-Disposition"] = 'attachment; filename="admin_finance_report.xls"'

    html = [
        "<table>",
        "<tr><th>Suborder ID</th><th>Order ID</th><th>Producer</th><th>Customer</th><th>Delivery date</th><th>Status</th><th>Order total</th><th>Order commission</th><th>Subtotal</th><th>Commission</th><th>Producer payout</th></tr>",
    ]
    for row in rows:
        html.append(
            "<tr>"
            f"<td>{row['suborder_id']}</td>"
            f"<td>{row['order_id']}</td>"
            f"<td>{row['producer']}</td>"
            f"<td>{row['customer']}</td>"
            f"<td>{row['delivery_date']}</td>"
            f"<td>{row['status']}</td>"
            f"<td>{row['order_total']}</td>"
            f"<td>{row['order_commission_total']}</td>"
            f"<td>{row['subtotal']}</td>"
            f"<td>{row['commission_amount']}</td>"
            f"<td>{row['producer_payout_amount']}</td>"
            "</tr>"
        )
    html.append("</table>")
    response.write("".join(html))
    return response


@login_required
def export_admin_finance_pdf(request):
    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    suborders = list(
        apply_finance_filters(
            base_finance_queryset(),
            status_filter=(request.GET.get("status") or "").strip(),
            producer_filter=(request.GET.get("producer") or "").strip(),
            q=(request.GET.get("q") or "").strip(),
            date_from=(request.GET.get("date_from") or "").strip(),
            date_to=(request.GET.get("date_to") or "").strip(),
        )
    )
    totals = aggregate_finance_totals(base_finance_queryset().filter(id__in=[s.id for s in suborders]))
    lines = [
        "Admin Finance Report",
        f"Rows exported: {len(suborders)}",
        f"Gross sales: GBP {totals['gross_sales']:.2f}",
        f"Commission: GBP {totals['total_commission']:.2f}",
        f"Producer payouts: GBP {totals['total_payouts']:.2f}",
        f"Status filter: {(request.GET.get('status') or '').strip() or 'All'}",
        f"Producer filter: {(request.GET.get('producer') or '').strip() or 'All'}",
        "",
    ]
    for row in build_admin_report_rows(suborders)[:30]:
        lines.append(
            f"Order {row['order_id']} / Suborder {row['suborder_id']} / {row['producer']} / "
            f"Subtotal {row['subtotal']:.2f} / Commission {row['commission_amount']:.2f} / "
            f"Payout {row['producer_payout_amount']:.2f}"
        )

    response = HttpResponse(build_finance_pdf(lines), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="admin_finance_report.pdf"'
    return response


@login_required
def admin_order_finance_detail(request, order_id):
    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    order = get_object_or_404(
        Order.objects.select_related("customer").prefetch_related("producer_suborders__items__product"),
        id=order_id,
    )
    suborders = list(
        base_finance_queryset().filter(order=order)
    )
    if not suborders:
        raise PermissionDenied("No producer finance records found for this order.")

    payment_snapshot = find_payment_snapshot_for_market_order(order)
    payment_card_type = ""
    if payment_snapshot:
        payment_card_type = PAYMENT_CARD_TYPE_LABELS.get(
            payment_snapshot.provider,
            payment_snapshot.provider.replace("_", " ").title(),
        )
    producer_payout_total = sum(
        (suborder.producer_payout_amount for suborder in suborders),
        Decimal("0.00"),
    )

    return render(
        request,
        "market_finance/admin_order_finance_detail.html",
        {
            "order": order,
            "suborders": suborders,
            "payment_snapshot": payment_snapshot,
            "payment_card_type": payment_card_type,
            "producer_payout_total": producer_payout_total,
        },
    )


@login_required
def export_settlement_csv(request):
    if not (is_admin(request.user) or is_producer(request.user)):
        raise PermissionDenied("Finance access required.")

    producer_id = (request.GET.get("producer_id") or "").strip()
    week_start = (request.GET.get("week_start") or "").strip()
    if not producer_id or not week_start:
        return HttpResponseBadRequest("Producer and week_start are required.")

    record = (
        Settlement.objects.filter(producer_id=producer_id, week_start=week_start)
        .prefetch_related("included_suborders__order__customer")
        .first()
    )
    matching = list(record.included_suborders.all()) if record else get_settlement_suborders(
        producer_id=producer_id,
        week_start=date.fromisoformat(week_start),
    )
    if not matching:
        return HttpResponseBadRequest("No delivered suborders found for that settlement.")

    if is_producer(request.user) and str(request.user.id) != producer_id:
        raise PermissionDenied("You can only export your own settlements.")

    producer = matching[0].producer
    week_end = (
        record.week_end.isoformat()
        if record
        else settlement_week_bounds(matching[0].delivery_date)[1].isoformat()
    )
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="settlement_{producer.username}_{week_start}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["producer", producer.business_name or producer.username])
    writer.writerow(["week_start", week_start])
    writer.writerow(["week_end", week_end])
    writer.writerow(["settlement_status", record.get_status_display() if record else "Pending generation"])
    writer.writerow([])
    writer.writerow([
        "suborder_id", "order_id", "customer", "delivery_date",
        "subtotal", "commission_amount", "producer_payout_amount",
    ])
    for suborder in matching:
        writer.writerow([
            suborder.id,
            suborder.order.id,
            suborder.order.customer.username,
            suborder.delivery_date.isoformat(),
            suborder.subtotal,
            suborder.commission_amount,
            suborder.producer_payout_amount,
        ])

    writer.writerow([])
    writer.writerow(["gross_sales", sum(s.subtotal for s in matching)])
    writer.writerow(["commission_amount", sum(s.commission_amount for s in matching)])
    writer.writerow(["payout_amount", sum(s.producer_payout_amount for s in matching)])
    return response


@login_required
def recurring_orders_dashboard(request):
    recurring_orders = []
    earliest_delivery = timezone.now() + timedelta(hours=48)
    for recurring_order in RecurringOrder.objects.filter(customer=request.user).order_by("next_run_date", "id"):
        recurring_order.preview = get_recurring_order_preview(recurring_order)
        recurring_order.next_instance_overrides_json = json.dumps(
            recurring_order.next_instance_overrides or {},
            indent=2,
        )
        recurring_order.earliest_delivery_date = earliest_delivery.date().isoformat()
        recurring_orders.append(recurring_order)
    return render(
        request,
        "market_finance/recurring_orders.html",
        {"recurring_orders": recurring_orders},
    )


@login_required
def create_recurring_order_from_order(request, order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    order = get_object_or_404(
        Order.objects.prefetch_related("producer_suborders__items"),
        id=order_id,
        customer=request.user,
    )
    template = build_recurring_template_from_order(order)
    preferred_delivery_time = template.get("preferred_delivery_time")
    recurring_order = RecurringOrder.objects.create(
        customer=request.user,
        template_order_data=template,
        preferred_delivery_time=datetime.strptime(preferred_delivery_time, "%H:%M").time() if preferred_delivery_time else None,
        next_run_date=timezone.localdate() + timedelta(days=7),
    )
    messages.success(request, "Your weekly repeat order has been created.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def update_recurring_order_next_instance(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    try:
        if "special_instructions" in request.POST:
            overrides = {"special_instructions": (request.POST.get("special_instructions") or "").strip()}
        else:
            raw_overrides = (request.POST.get("overrides_json") or "").strip()
            overrides = json.loads(raw_overrides or "{}")
        update_next_instance_overrides(recurring_order, overrides)
    except Exception:
        messages.error(request, "We could not update your note. Please try again.")
    else:
        messages.success(request, "Your next repeat order has been updated.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def change_recurring_order_status(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    status = (request.POST.get("status") or "").strip().upper()
    friendly_status_messages = {
        RecurringOrder.Status.ACTIVE: "Your repeat order has been resumed.",
        RecurringOrder.Status.PAUSED: "Your repeat order has been paused.",
        RecurringOrder.Status.CANCELLED: "Your repeat order has been cancelled.",
    }
    try:
        update_recurring_order_status(recurring_order, status)
    except ValidationError:
        messages.error(request, "We could not update this repeat order. Please try again.")
    except Exception:
        messages.error(request, "Something went wrong while updating this repeat order.")
    else:
        messages.success(request, friendly_status_messages.get(status, "Your repeat order has been updated."))
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def update_recurring_order_time(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    delivery_date = (request.POST.get("next_run_date") or "").strip()
    delivery_time = (request.POST.get("preferred_delivery_time") or "").strip()
    try:
        update_recurring_order_delivery_schedule(recurring_order, delivery_date, delivery_time)
    except ValidationError:
        messages.error(request, "Please choose a delivery day and time at least 48 hours from now.")
    except Exception:
        messages.error(request, "We could not update the delivery day and time. Please try again.")
    else:
        messages.success(request, "Your delivery day and time have been updated.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def update_recurring_order_details_view(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    quantities = {
        key.removeprefix("quantity_"): value
        for key, value in request.POST.items()
        if key.startswith("quantity_")
    }
    try:
        update_recurring_order_details(
            recurring_order,
            delivery_address=request.POST.get("delivery_address", ""),
            customer_phone=request.POST.get("customer_phone", ""),
            special_instructions=request.POST.get("special_instructions", ""),
            quantities=quantities,
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0] if getattr(exc, "messages", None) else "Please check the repeat order details.")
    except Exception:
        messages.error(request, "We could not update this repeat order. Please try again.")
    else:
        messages.success(request, "Your repeat order has been updated.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def run_recurring_order_now(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    try:
        order = generate_order_from_recurring(recurring_order)
    except ValidationError:
        messages.error(request, "This repeat order cannot be placed right now. Please check the items and try again.")
    except Exception:
        messages.error(request, "We could not place this repeat order. Please try again.")
    else:
        messages.success(request, f"Order #{order.id} has been placed from your repeat order.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def run_due_recurring_orders_now(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    due_orders = RecurringOrder.objects.filter(customer=request.user, status=RecurringOrder.Status.ACTIVE, next_run_date__lte=timezone.localdate())
    generated = 0
    for recurring_order in due_orders:
        try:
            generate_order_from_recurring(recurring_order)
        except Exception:
            messages.error(request, "One of your repeat orders could not be placed.")
        else:
            generated += 1

    if generated:
        messages.success(request, "Your due repeat orders have been placed.")
    elif not due_orders.exists():
        messages.info(request, "No due recurring orders were ready to generate.")
    return redirect("market_finance:recurring_orders_dashboard")
