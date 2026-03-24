from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render

from accounts.permissions import can_manage_producer_orders
from .models import ProducerSubOrder, SubOrderStatusEvent
from .services import get_allowed_next_statuses, transition_suborder


def _ensure_producer_access(user):
    if not can_manage_producer_orders(user):
        raise PermissionDenied("Producer access required.")


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
