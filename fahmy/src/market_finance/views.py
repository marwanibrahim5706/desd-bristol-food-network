import csv
import json
import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
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
    build_finance_pdf,
    build_order_finance_summaries,
    build_recurring_template_from_order,
    build_settlement_summaries,
    calculate_running_period_summaries,
    find_payment_snapshot_for_market_order,
    generate_order_from_recurring,
    generate_weekly_settlements,
    settlement_week_bounds,
    update_next_instance_overrides,
)


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


def _build_settlement_periods(summaries, producers):
    if not summaries:
        return []

    summary_map = {
        (summary["producer"].id, summary["week_start"]): summary
        for summary in summaries
    }
    represented_months = sorted({_month_start(summary["week_start"]) for summary in summaries})
    periods = []

    for month_start in represented_months:
        month_end = _month_end(month_start)
        month_summaries = [
            summary for summary in summaries
            if _month_start(summary["week_start"]) == month_start
        ]
        month_starts = sorted({summary["week_start"] for summary in month_summaries})
        if not month_starts:
            continue

        cursor = month_start
        existing_ranges = {summary["week_start"]: summary["week_end"] for summary in month_summaries}

        for index, start in enumerate(month_starts):
            if cursor < start:
                gap_end = start - timedelta(days=1)
                periods.extend(
                    _split_settlement_gap(
                        cursor,
                        gap_end,
                        preserve_full_gap=(index == 0),
                    )
                )
            periods.append((start, existing_ranges[start]))
            cursor = existing_ranges[start] + timedelta(days=1)

        if cursor <= month_end:
            trailing_periods = _split_settlement_gap(cursor, month_end)
            # A very short trailing fragment at month-end belongs more naturally
            # to the next month's first full week than as a standalone period.
            if trailing_periods:
                last_start, last_end = trailing_periods[-1]
                if (last_end - last_start).days + 1 < 4:
                    trailing_periods = trailing_periods[:-1]
            periods.extend(trailing_periods)

    expanded = []
    seen = set()
    for week_start, week_end in periods:
        if (week_start, week_end) in seen:
            continue
        seen.add((week_start, week_end))
        for producer in producers:
            existing = summary_map.get((producer.id, week_start))
            if existing is not None:
                existing["has_data"] = True
                expanded.append(existing)
                continue

            expanded.append(
                {
                    "producer": producer,
                    "week_start": week_start,
                    "week_end": week_end,
                    "gross_sales": Decimal("0.00"),
                    "commission": Decimal("0.00"),
                    "payout": Decimal("0.00"),
                    "suborder_count": 0,
                    "suborders": [],
                    "has_data": False,
                }
            )

    return sorted(
        expanded,
        key=lambda settlement: (
            settlement["week_start"],
            (getattr(settlement["producer"], "business_name", "") or settlement["producer"].username).lower(),
        ),
        reverse=True,
    )


@login_required
def admin_finance_dashboard(request):
    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    status_filter = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    producer_filter = (request.GET.get("producer") or "").strip()
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    settlement_week = (request.GET.get("settlement_week") or "").strip()

    suborders_qs = apply_finance_filters(
        base_finance_queryset(),
        status_filter=status_filter,
        producer_filter=producer_filter,
        q=q,
        date_from=date_from,
        date_to=date_to,
    )

    suborders = list(suborders_qs)
    order_summaries = build_order_finance_summaries(suborders)
    aggregates = aggregate_finance_totals(suborders_qs)
    order_count = len({suborder.order_id for suborder in suborders})
    settlement_source_qs = base_finance_queryset().filter(status=ProducerSubOrder.Status.DELIVERED)
    if producer_filter:
        settlement_source_qs = settlement_source_qs.filter(producer_id=producer_filter)
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

    settlement_summaries = _build_settlement_periods(
        build_settlement_summaries(list(settlement_source_qs)),
        producer_users,
    )
    running_summaries = calculate_running_period_summaries()

    settlement_map = {
        (settlement.producer_id, settlement.week_start): settlement
        for settlement in Settlement.objects.select_related("producer")
    }
    all_settlements = []
    for summary in settlement_summaries:
        stored_settlement = settlement_map.get((summary["producer"].id, summary["week_start"]))
        summary["record"] = stored_settlement
        all_settlements.append(summary)

    available_weeks, previous_week, next_week = _settlement_week_navigation(all_settlements, settlement_week)
    settlements = all_settlements
    if settlement_week:
        settlements = [
            settlement for settlement in all_settlements
            if settlement["week_start"].isoformat() == settlement_week
        ]

    return render(
        request,
        "market_finance/admin_finance_dashboard.html",
        {
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
            "available_weeks": available_weeks,
            "previous_week_query": _querystring_with_week(request, previous_week),
            "next_week_query": _querystring_with_week(request, next_week),
            "order_summaries": order_summaries,
            "month_summary": running_summaries["month"],
            "ytd_summary": running_summaries["ytd"],
        },
    )


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
        messages.success(request, f"Settlement created for {producer} ({start_date}).")
    else:
        messages.success(request, f"Settlement refreshed for {producer} ({start_date}).")

    return redirect("market_finance:admin_finance_dashboard")


@login_required
def mark_settlement_paid(request, settlement_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    if not is_admin(request.user):
        raise PermissionDenied("Admin access required.")

    settlement = get_object_or_404(Settlement, id=settlement_id)
    settlement.status = Settlement.Status.PAID
    settlement.paid_at = timezone.now()
    settlement.save(update_fields=["status", "paid_at"])
    messages.success(request, f"Settlement #{settlement.id} marked as paid.")
    return redirect("market_finance:admin_finance_dashboard")


@login_required
def producer_settlements_dashboard(request):
    if not is_producer(request.user):
        raise PermissionDenied("Producer access required.")

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    settlement_week = (request.GET.get("settlement_week") or "").strip()

    filtered_delivered_qs = apply_finance_filters(
        base_finance_queryset().filter(producer=request.user),
        status_filter=ProducerSubOrder.Status.DELIVERED,
        date_from=date_from,
        date_to=date_to,
    )
    suborders_qs = filtered_delivered_qs
    suborders = list(suborders_qs)
    all_settlements = _build_settlement_periods(
        build_settlement_summaries(
            list(
                base_finance_queryset().filter(
                    producer=request.user,
                    status=ProducerSubOrder.Status.DELIVERED,
                )
            )
        ),
        [request.user],
    )
    settlement_map = {
        settlement.week_start: settlement
        for settlement in Settlement.objects.filter(producer=request.user)
    }
    for summary in all_settlements:
        summary["record"] = settlement_map.get(summary["week_start"])

    available_weeks, previous_week, next_week = _settlement_week_navigation(all_settlements, settlement_week)
    settlements = all_settlements
    if settlement_week:
        settlements = [
            settlement for settlement in all_settlements
            if settlement["week_start"].isoformat() == settlement_week
        ]

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
    writer.writerow([
        "suborder_id", "order_id", "producer", "customer", "delivery_date",
        "status", "subtotal", "commission_amount", "producer_payout_amount",
    ])
    for row in build_admin_report_rows(list(suborders_qs)):
        writer.writerow([
            row["suborder_id"],
            row["order_id"],
            row["producer"],
            row["customer"],
            row["delivery_date"],
            row["status"],
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
        "<tr><th>Suborder ID</th><th>Order ID</th><th>Producer</th><th>Customer</th><th>Delivery date</th><th>Status</th><th>Subtotal</th><th>Commission</th><th>Producer payout</th></tr>",
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

    qs = base_finance_queryset().filter(
        producer_id=producer_id,
        status=ProducerSubOrder.Status.DELIVERED,
    )
    matching = [
        suborder for suborder in qs
        if settlement_week_bounds(suborder.delivery_date)[0].isoformat() == week_start
    ]
    if not matching:
        return HttpResponseBadRequest("No delivered suborders found for that settlement.")

    if is_producer(request.user) and str(request.user.id) != producer_id:
        raise PermissionDenied("You can only export your own settlements.")

    producer = matching[0].producer
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="settlement_{producer.username}_{week_start}.csv"'
    )
    writer = csv.writer(response)
    writer.writerow(["producer", producer.business_name or producer.username])
    writer.writerow(["week_start", week_start])
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
    recurring_orders = RecurringOrder.objects.filter(customer=request.user).order_by("next_run_date", "id")
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
    recurring_order = RecurringOrder.objects.create(
        customer=request.user,
        template_order_data=build_recurring_template_from_order(order),
        next_run_date=timezone.localdate() + timedelta(days=7),
    )
    messages.success(request, f"Recurring order #{recurring_order.id} created.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def update_recurring_order_next_instance(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    raw_overrides = (request.POST.get("overrides_json") or "").strip()
    try:
        overrides = json.loads(raw_overrides or "{}")
        update_next_instance_overrides(recurring_order, overrides)
    except Exception as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Next instance updated for recurring order #{recurring_order.id}.")
    return redirect("market_finance:recurring_orders_dashboard")


@login_required
def run_recurring_order_now(request, recurring_order_id):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required.")

    recurring_order = get_object_or_404(RecurringOrder, id=recurring_order_id, customer=request.user)
    try:
        order = generate_order_from_recurring(recurring_order)
    except Exception as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, f"Recurring order generated as order #{order.id}.")
    return redirect("market_finance:recurring_orders_dashboard")
