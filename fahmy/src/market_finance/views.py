import csv
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import is_admin, is_producer
from market_orders.models import ProducerSubOrder
from market_payments.models import Settlement
from .services import (
    aggregate_finance_totals,
    apply_finance_filters,
    base_finance_queryset,
    build_settlement_summaries,
    settlement_week_bounds,
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
                "label": f'{settlement["week_start"].strftime("%d %b %Y")} - {settlement["week_end"].strftime("%d %b %Y")}',
            }
        )
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
    params = request.GET.copy()
    if week_value:
        params["settlement_week"] = week_value
    else:
        params.pop("settlement_week", None)
    encoded = params.urlencode()
    return f"?{encoded}" if encoded else ""


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
    aggregates = aggregate_finance_totals(suborders_qs)
    delivered_count = sum(1 for suborder in suborders if suborder.status == ProducerSubOrder.Status.DELIVERED)
    settlement_summaries = build_settlement_summaries(suborders)

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

    producers = (
        ProducerSubOrder.objects.select_related("producer")
        .order_by("producer__username")
        .values("producer_id", "producer__username", "producer__business_name")
        .distinct()
    )

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
            "delivered_count": delivered_count,
            "available_weeks": available_weeks,
            "previous_week_query": _querystring_with_week(request, previous_week),
            "next_week_query": _querystring_with_week(request, next_week),
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

    producer = matching[0].producer
    start_date = datetime.fromisoformat(week_start).date()
    end_date = start_date + timedelta(days=6)
    gross_sales = sum((suborder.subtotal for suborder in matching), Decimal("0.00"))
    commission = sum((suborder.commission_amount for suborder in matching), Decimal("0.00"))
    payout = sum((suborder.producer_payout_amount for suborder in matching), Decimal("0.00"))

    settlement, created = Settlement.objects.update_or_create(
        producer=producer,
        week_start=start_date,
        defaults={
            "week_end": end_date,
            "gross_sales": gross_sales,
            "commission_amount": commission,
            "payout_amount": payout,
            "suborder_count": len(matching),
            "generated_by": request.user,
            "status": Settlement.Status.GENERATED,
            "paid_at": None,
        },
    )

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

    suborders_qs = apply_finance_filters(
        base_finance_queryset().filter(producer=request.user),
        status_filter=ProducerSubOrder.Status.DELIVERED,
        date_from=date_from,
        date_to=date_to,
    )
    suborders = list(suborders_qs)
    all_settlements = build_settlement_summaries(suborders)
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
    for suborder in suborders_qs:
        writer.writerow([
            suborder.id,
            suborder.order.id,
            suborder.producer.business_name or suborder.producer.username,
            suborder.order.customer.username,
            suborder.delivery_date.isoformat(),
            suborder.status,
            suborder.subtotal,
            suborder.commission_amount,
            suborder.producer_payout_amount,
        ])
    return response


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
