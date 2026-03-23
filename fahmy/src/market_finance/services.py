from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from market_orders.models import ProducerSubOrder


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
