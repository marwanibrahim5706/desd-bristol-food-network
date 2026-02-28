from django.db import models
from django.conf import settings

class Notification(models.Model):
    class Type(models.TextChoices):
        LOW_STOCK = "LOW_STOCK", "Low Stock"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications"
    )

    product = models.ForeignKey(
        "market_products.Product",
        on_delete=models.CASCADE
    )

    type = models.CharField(max_length=20, choices=Type.choices)
    message = models.TextField()

    is_resolved = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
