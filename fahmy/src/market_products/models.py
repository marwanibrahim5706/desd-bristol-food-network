from django.conf import settings
from django.db import models


class Product(models.Model):
    CATEGORY_CHOICES = [
        ("fruit_veg", "Fruit & Vegetables"),
        ("dairy", "Dairy"),
        ("bakery", "Bakery"),
        ("meat", "Meat"),
        ("drinks", "Drinks"),
        ("other", "Other"),
    ]

    name = models.CharField(max_length=255)

    producer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="products",
    )

    description = models.TextField(blank=True)
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default="other")

    price = models.DecimalField(max_digits=10, decimal_places=2)

    stock_quantity = models.PositiveIntegerField(default=0)
    low_stock_threshold = models.PositiveIntegerField(default=5)

    is_active = models.BooleanField(default=True)
    allergens = models.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name

    @property
    def is_low_stock(self):
        return self.stock_quantity <= self.low_stock_threshold