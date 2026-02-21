from django.conf import settings
from django.db import models


class Order(models.Model):
    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        CONFIRMED = "CONFIRMED", "Confirmed"
        CANCELLED = "CANCELLED", "Cancelled"
        COMPLETED = "COMPLETED", "Completed"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders"
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Order #{self.id}"


class ProducerSubOrder(models.Model):
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="producer_suborders"
    )

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="producer_orders"
    )

    status = models.CharField(
        max_length=20,
        choices=Order.Status.choices,
        default=Order.Status.CREATED
    )

    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)


class OrderItem(models.Model):
    suborder = models.ForeignKey(
        ProducerSubOrder,
        on_delete=models.CASCADE,
        related_name="items"
    )

    product_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField()

    def get_total(self):
        return self.unit_price * self.quantity