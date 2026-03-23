from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from market_orders.models import Order, OrderItem, ProducerSubOrder

from .models import Product, Review
from .services import create_verified_review, user_can_review_product


class VerifiedReviewServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="customer_review",
            password="secret123",
            role=user_model.Role.CUSTOMER,
        )
        self.producer = user_model.objects.create_user(
            username="producer_review",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Review Farm",
        )
        self.product = Product.objects.create(
            name="Review Apples",
            producer=self.producer,
            price=Decimal("3.50"),
            stock_quantity=50,
            is_active=True,
        )

    def test_user_can_review_only_after_delivered_purchase(self):
        order = Order.objects.create(
            customer=self.customer,
            status=Order.Status.CREATED,
            total_amount=Decimal("7.00"),
        )
        suborder = ProducerSubOrder.objects.create(
            order=order,
            producer=self.producer,
            status=ProducerSubOrder.Status.CONFIRMED,
            delivery_date=timezone.now() + timedelta(days=1),
            subtotal=Decimal("7.00"),
            commission_amount=Decimal("0.35"),
            producer_payout_amount=Decimal("6.65"),
        )
        OrderItem.objects.create(
            suborder=suborder,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=2,
        )

        self.assertFalse(user_can_review_product(self.customer, self.product))

        suborder.status = ProducerSubOrder.Status.DELIVERED
        suborder.save(update_fields=["status"])

        self.assertTrue(user_can_review_product(self.customer, self.product))

    def test_review_is_limited_to_one_per_user_per_product(self):
        order = Order.objects.create(
            customer=self.customer,
            status=Order.Status.CREATED,
            total_amount=Decimal("3.50"),
        )
        suborder = ProducerSubOrder.objects.create(
            order=order,
            producer=self.producer,
            status=ProducerSubOrder.Status.DELIVERED,
            delivery_date=timezone.now(),
            subtotal=Decimal("3.50"),
            commission_amount=Decimal("0.18"),
            producer_payout_amount=Decimal("3.32"),
        )
        OrderItem.objects.create(
            suborder=suborder,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=1,
        )

        review = create_verified_review(
            user=self.customer,
            product=self.product,
            rating=5,
            comment="Excellent",
        )
        self.assertEqual(review.rating, 5)
        self.assertEqual(Review.objects.count(), 1)

        with self.assertRaises(ValidationError):
            create_verified_review(
                user=self.customer,
                product=self.product,
                rating=4,
                comment="Second review",
            )
