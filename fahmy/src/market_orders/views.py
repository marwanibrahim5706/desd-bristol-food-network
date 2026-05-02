from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from accounts.permissions import can_manage_producer_orders
from market_finance.models import Settlement
from market_products.models import Product
from .models import ProducerSubOrder, SubOrderStatusEvent
from .services import get_allowed_next_statuses, transition_suborder


def _ensure_producer_access(user):
    if not can_manage_producer_orders(user):
        raise PermissionDenied("Producer access required.")


@login_required
def producer_home(request):
    _ensure_producer_access(request.user)

    open_statuses = [
        ProducerSubOrder.Status.PENDING,
        ProducerSubOrder.Status.CONFIRMED,
        ProducerSubOrder.Status.READY,
    ]
    producer_suborders = ProducerSubOrder.objects.filter(producer=request.user)
    products = Product.objects.filter(producer=request.user)

    open_orders_count = producer_suborders.filter(status__in=open_statuses).count()
    needs_action_count = producer_suborders.filter(status=ProducerSubOrder.Status.PENDING).count()
    ready_count = producer_suborders.filter(status=ProducerSubOrder.Status.READY).count()
    delivered_count = producer_suborders.filter(status=ProducerSubOrder.Status.DELIVERED).count()
    active_products_count = products.filter(is_active=True).count()
    low_stock_products = [product for product in products if product.is_low_stock]
    total_products_count = products.count()
    stock_ok_count = max(total_products_count - len(low_stock_products), 0)

    generated_payout = Settlement.objects.filter(
        producer=request.user,
        status=Settlement.Status.GENERATED,
    ).aggregate(total=Sum("payout_amount"))["total"] or 0
    delivered_payout = producer_suborders.filter(
        status=ProducerSubOrder.Status.DELIVERED,
    ).aggregate(total=Sum("producer_payout_amount"))["total"] or 0
    upcoming_payout = generated_payout or delivered_payout
    payout_progress = 100
    if delivered_payout:
        payout_progress = min(round((generated_payout / delivered_payout) * 100), 100) if generated_payout else 35

    latest_orders = (
        producer_suborders.select_related("order", "order__customer")
        .prefetch_related("items")
        .order_by("delivery_date")[:3]
    )
    recent_events = (
        SubOrderStatusEvent.objects.filter(suborder__producer=request.user)
        .select_related("suborder", "suborder__order", "changed_by")
        .order_by("-changed_at")[:5]
    )
    latest_settlement = Settlement.objects.filter(producer=request.user).order_by("-week_start", "-id").first()
    status_breakdown = [
        {"label": "Pending", "count": needs_action_count, "tone": "pending"},
        {"label": "Confirmed", "count": producer_suborders.filter(status=ProducerSubOrder.Status.CONFIRMED).count(), "tone": "confirmed"},
        {"label": "Ready", "count": ready_count, "tone": "ready"},
        {"label": "Delivered", "count": delivered_count, "tone": "delivered"},
    ]
    max_status_count = max([item["count"] for item in status_breakdown] + [1])
    for item in status_breakdown:
        item["percent"] = round((item["count"] / max_status_count) * 100) if item["count"] else 4

    today = timezone.localdate()
    trend_days = []
    for offset in range(7):
        day = today + timedelta(days=offset)
        count = producer_suborders.filter(delivery_date__date=day).count()
        trend_days.append({"label": day.strftime("%a"), "count": count})
    max_trend_count = max([day["count"] for day in trend_days] + [1])
    for day in trend_days:
        day["height"] = round((day["count"] / max_trend_count) * 100) if day["count"] else 8

    stock_health_percent = round((stock_ok_count / total_products_count) * 100) if total_products_count else 100

    return render(
        request,
        "market_orders/producer_home.html",
        {
            "open_orders_count": open_orders_count,
            "needs_action_count": needs_action_count,
            "ready_count": ready_count,
            "delivered_count": delivered_count,
            "active_products_count": active_products_count,
            "low_stock_count": len(low_stock_products),
            "low_stock_products": low_stock_products[:4],
            "stock_health_percent": stock_health_percent,
            "stock_ok_count": stock_ok_count,
            "upcoming_payout": upcoming_payout,
            "generated_payout": generated_payout,
            "delivered_payout": delivered_payout,
            "payout_progress": payout_progress,
            "status_breakdown": status_breakdown,
            "trend_days": trend_days,
            "latest_orders": latest_orders,
            "recent_events": recent_events,
            "latest_settlement": latest_settlement,
        },
    )


@login_required
def producer_dashboard(request):
    _ensure_producer_access(request.user)

    status_filter = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = ProducerSubOrder.objects.filter(producer=request.user)

    if status_filter:
        qs = qs.filter(status=status_filter)

    if q:
        qs = qs.filter(
            Q(order__id__icontains=q)
            | Q(order__customer__username__icontains=q)
            | Q(order__customer__email__icontains=q)
        )

    qs = (
        qs.select_related("order", "order__customer")
        .prefetch_related(
            "items",
            Prefetch(
                "status_events",
                queryset=SubOrderStatusEvent.objects.select_related("changed_by").order_by("-changed_at"),
            ),
        )
        .order_by("delivery_date")
    )

    min_lead = timedelta(hours=48)
    suborders = []
    for suborder in qs:
        lead_ok = (suborder.delivery_date - suborder.order.created_at) >= min_lead
        suborder.allowed_next = get_allowed_next_statuses(suborder.status)
        suborders.append((suborder, lead_ok))

    return render(
        request,
        "market_orders/producer_dashboard.html",
        {
            "suborders": suborders,
            "status_filter": status_filter,
            "q": q,
            "status_choices": ProducerSubOrder.Status.choices,
        },
    )


@login_required
def producer_suborder_detail(request, suborder_id):
    _ensure_producer_access(request.user)

    base_qs = (
        ProducerSubOrder.objects.select_related("order", "order__customer")
        .prefetch_related("items", "status_events")
    )
    suborder = get_object_or_404(base_qs, id=suborder_id, producer=request.user)

    lead_ok = (suborder.delivery_date - suborder.order.created_at) >= timedelta(hours=48)
    allowed_next = get_allowed_next_statuses(suborder.status)
    status_events = suborder.status_events.select_related("changed_by").order_by("-changed_at")

    return render(
        request,
        "market_orders/producer_suborder_detail.html",
        {
            "suborder": suborder,
            "lead_ok": lead_ok,
            "allowed_next": allowed_next,
            "status_events": status_events,
            "status_choices": ProducerSubOrder.Status.choices,
        },
    )


@login_required
def producer_suborder_change_status(request, suborder_id):
    if request.method != "POST":
        raise PermissionDenied("POST required")

    _ensure_producer_access(request.user)

    base_qs = ProducerSubOrder.objects.select_related("order", "order__customer")
    suborder = get_object_or_404(base_qs, id=suborder_id, producer=request.user)

    new_status = (request.POST.get("new_status") or "").strip()
    note = (request.POST.get("note") or "").strip()

    try:
        transition_suborder(
            suborder=suborder,
            new_status=new_status,
            actor=request.user,
            note=note,
        )
        messages.success(request, f"Status updated to {new_status}.")
    except ValidationError as exc:
        messages.error(request, str(exc))
    except Exception as exc:
        messages.error(request, f"Failed to update status: {exc}")

    return redirect(f"/orders/producer/dashboard/#suborder-card-{suborder.id}")
