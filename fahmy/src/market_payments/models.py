from decimal import Decimal
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class Product(models.Model):
    name = models.CharField(max_length=120)
    price = models.DecimalField(max_digits=10, decimal_places=2)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class Cart(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="cart")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart for {self.user}"

    @property
    def subtotal(self):
        total = Decimal("0.00")
        for item in self.items.all():
            total += item.line_total
        return total.quantize(Decimal("0.01"))

    @property
    def commission(self):
        return (self.subtotal * Decimal("0.05")).quantize(Decimal("0.01"))

    @property
    def total(self):
        return (self.subtotal + self.commission).quantize(Decimal("0.01"))


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("market_products.Product", on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ("cart", "product")

    def __str__(self):
        return f"{self.product} x{self.quantity}"

    @property
    def line_total(self):
        return (self.product.price * self.quantity).quantize(Decimal("0.01"))


class Order(models.Model):
    user = models.ForeignKey(
    User,
    on_delete=models.CASCADE,
    related_name="payment_orders",
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2)
    commission = models.DecimalField(max_digits=10, decimal_places=2)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Order #{self.id} - £{self.total}"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name="items")
    product_name = models.CharField(max_length=120)
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    quantity = models.PositiveIntegerField(default=1)

    @property
    def line_total(self):
        return (self.unit_price * self.quantity).quantize(Decimal("0.01"))


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    subtotal = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    producer_payout_amount = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    provider = models.CharField(max_length=50, default="demo")
    transaction_reference = models.CharField(max_length=120, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Payment #{self.id} - {self.status}"

    @property
    def payment_status(self):
        """Compatibility alias for reporting language."""
        return self.status


class Settlement(models.Model):
    class Status(models.TextChoices):
        GENERATED = "GENERATED", "Generated"
        FAILED = "FAILED", "Payout failed"
        PAID = "PAID", "Paid"

    producer = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="settlements",
    )
    included_suborders = models.ManyToManyField(
        "market_orders.ProducerSubOrder",
        related_name="settlements",
        blank=True,
    )
    week_start = models.DateField()
    week_end = models.DateField()
    gross_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    payout_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    suborder_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.GENERATED)
    generated_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="generated_settlements",
    )
    generated_at = models.DateTimeField(auto_now_add=True)
    payout_provider = models.CharField(max_length=50, blank=True, default="")
    payout_reference = models.CharField(max_length=120, blank=True, default="")
    payout_error = models.TextField(blank=True, default="")
    payout_requested_at = models.DateTimeField(blank=True, null=True)
    paid_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ["-week_start", "producer__username"]
        constraints = [
            models.UniqueConstraint(
                fields=["producer", "week_start"],
                name="unique_settlement_per_producer_week",
            )
        ]

    def __str__(self):
        return f"Settlement #{self.id} - {self.producer} - {self.week_start}"

    @property
    def total_orders_value(self):
        """Compatibility alias for gross settlement value before commission."""
        return self.gross_sales

    @property
    def total_commission(self):
        """Compatibility alias for total platform commission."""
        return self.commission_amount

    @property
    def total_payout(self):
        """Compatibility alias for total producer payout."""
        return self.payout_amount

    @property
    def payout_receipt_reference(self):
        if (
            self.payout_provider == "external_payout_api"
            and self.payout_reference
            and not self.payout_reference.startswith("DEMO-")
        ):
            return self.payout_reference
        return ""
