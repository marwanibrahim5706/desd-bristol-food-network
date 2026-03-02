from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError

from .models import ProducerSubOrder, SubOrderStatusEvent


ALLOWED_TRANSITIONS = {
    ProducerSubOrder.Status.PENDING: {ProducerSubOrder.Status.CONFIRMED, ProducerSubOrder.Status.CANCELLED},
    ProducerSubOrder.Status.CONFIRMED: {ProducerSubOrder.Status.READY, ProducerSubOrder.Status.CANCELLED},
    ProducerSubOrder.Status.READY: {ProducerSubOrder.Status.DELIVERED, ProducerSubOrder.Status.CANCELLED},
    ProducerSubOrder.Status.DELIVERED: set(),
    ProducerSubOrder.Status.CANCELLED: set(),
}


def get_allowed_next_statuses(current_status: str):
    return sorted(ALLOWED_TRANSITIONS.get(current_status, set()))


@transaction.atomic
def transition_suborder(*, suborder: ProducerSubOrder, new_status: str, actor, note: str = "") -> ProducerSubOrder:
    """
    TC-010: controlled lifecycle transitions + audit trail.
    """
    old_status = suborder.status

    allowed = ALLOWED_TRANSITIONS.get(old_status, set())
    if new_status not in allowed:
        raise ValidationError(f"Invalid transition: {old_status} -> {new_status}")

    # Audit log first (still in same transaction)
    SubOrderStatusEvent.objects.create(
        suborder=suborder,
        old_status=old_status,
        new_status=new_status,
        note=note or "",
        changed_by=actor,
    )

    suborder.status = new_status
    suborder.save(update_fields=["status", "updated_at"])
    return suborder