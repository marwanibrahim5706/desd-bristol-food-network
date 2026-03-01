from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, render
from accounts.permissions import is_admin
from .models import ProducerSubOrder


@login_required
def producer_dashboard(request):
    """
    TC9:
    - Producer views ONLY their incoming ProducerSubOrders
    - Can filter by status
    - Can search by customer username/email or order id
    - Sorted by delivery_date
    - Shows whether each suborder respects 48h lead time from order.created_at to delivery_date
    """

    # GET params
    status_filter = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()

    # Base queryset: ONLY this producer
    qs = ProducerSubOrder.objects.all() if is_admin(request.user) else ProducerSubOrder.objects.filter(producer=request.user)

    # Optional status filter
    if status_filter:
        qs = qs.filter(status=status_filter)

    # Optional search
    if q:
        qs = qs.filter(
            Q(order__id__icontains=q)
            | Q(order__customer__username__icontains=q)
            | Q(order__customer__email__icontains=q)
        )

    # Performance + sorting
    qs = (
        qs.select_related("order", "order__customer")
        .prefetch_related("items")
        .order_by("delivery_date")
    )

    # 48-hour lead time check
    min_lead = timedelta(hours=48)
    suborders = []
    for s in qs:
        lead_ok = (s.delivery_date - s.order.created_at) >= min_lead
        suborders.append((s, lead_ok))

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
    """
    TC9 details page:
    - Producer can open ONLY their own suborder
    - Shows customer + delivery info + items + totals
    - Shows 48h lead time check
    """

    base_qs = ProducerSubOrder.objects.select_related("order", "order__customer").prefetch_related("items")

    if is_admin(request.user):
        suborder = get_object_or_404(base_qs, id=suborder_id)
    else:
        suborder = get_object_or_404(base_qs, id=suborder_id, producer=request.user)

    lead_ok = (suborder.delivery_date - suborder.order.created_at) >= timedelta(hours=48)

    return render(
        request,
        "market_orders/producer_suborder_detail.html",
        {
            "suborder": suborder,
            "lead_ok": lead_ok,
        },
    )