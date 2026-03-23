from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from market_payments.models import Settlement as PaymentSettlement


class Settlement(PaymentSettlement):
    class Meta:
        proxy = True
        verbose_name = "Settlement"
        verbose_name_plural = "Settlements"


class RecurringOrder(models.Model):
    class Frequency(models.TextChoices):
        WEEKLY = "WEEKLY", "Weekly"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="recurring_orders",
    )
    frequency = models.CharField(
        max_length=20,
        choices=Frequency.choices,
        default=Frequency.WEEKLY,
    )
    template_order_data = models.JSONField(default=dict)
    next_instance_overrides = models.JSONField(default=dict, blank=True)
    next_run_date = models.DateField()
    active = models.BooleanField(default=True)
    last_run_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_run_date", "id"]

    def __str__(self):
        return f"RecurringOrder #{self.id} for {self.customer}"

    def clean(self):
        super().clean()
        if self.frequency != self.Frequency.WEEKLY:
            raise ValidationError({"frequency": "Only weekly recurring orders are supported."})

        if not isinstance(self.template_order_data, dict):
            raise ValidationError({"template_order_data": "Template order data must be a JSON object."})

        items = self.template_order_data.get("items", [])
        if not isinstance(items, list) or not items:
            raise ValidationError({"template_order_data": "Template order data must include at least one item."})

        if not isinstance(self.next_instance_overrides, dict):
            raise ValidationError({"next_instance_overrides": "Next instance overrides must be a JSON object."})
