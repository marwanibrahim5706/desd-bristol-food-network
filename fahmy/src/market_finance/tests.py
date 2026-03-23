from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from market_orders.models import Order, OrderItem, ProducerSubOrder
from market_payments.models import Settlement
from market_products.models import Product

from .models import RecurringOrder
from .services import generate_order_from_recurring, generate_weekly_settlements, settlement_week_bounds


class SettlementGenerationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="finance_admin",
            password="secret123",
            role=user_model.Role.ADMIN,
        )
        self.customer = user_model.objects.create_user(
            username="finance_customer",
            password="secret123",
            role=user_model.Role.CUSTOMER,
        )
        self.producer = user_model.objects.create_user(
            username="finance_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Finance Farm",
        )
        order = Order.objects.create(
            customer=self.customer,
            status=Order.Status.CREATED,
            total_amount=Decimal("50.00"),
            commission_total=Decimal("2.50"),
        )
        self.suborder = ProducerSubOrder.objects.create(
            order=order,
            producer=self.producer,
            status=ProducerSubOrder.Status.DELIVERED,
            delivery_date=timezone.now(),
            subtotal=Decimal("50.00"),
            commission_amount=Decimal("2.50"),
            producer_payout_amount=Decimal("47.50"),
        )

    def test_generate_weekly_settlements_persists_summary(self):
        settlements = generate_weekly_settlements(
            actor=self.admin,
            producer=self.producer,
            week_start=settlement_week_bounds(self.suborder.delivery_date)[0],
        )

        self.assertEqual(len(settlements), 1)
        settlement = Settlement.objects.get()
        self.assertEqual(settlement.gross_sales, Decimal("50.00"))
        self.assertEqual(settlement.commission_amount, Decimal("2.50"))
        self.assertEqual(settlement.payout_amount, Decimal("47.50"))


class RecurringOrderGenerationTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="recurring_customer",
            password="secret123",
            role=user_model.Role.CUSTOMER,
            address="1 Weekly Street",
            phone="07000000000",
        )
        self.producer = user_model.objects.create_user(
            username="recurring_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Weekly Farm",
        )
        self.product = Product.objects.create(
            name="Weekly Apples",
            producer=self.producer,
            price=Decimal("4.00"),
            stock_quantity=10,
            is_active=True,
        )

    def test_generate_order_from_recurring_creates_multi_vendor_rows(self):
        recurring_order = RecurringOrder.objects.create(
            customer=self.customer,
            next_run_date=date(2026, 3, 30),
            template_order_data={
                "delivery_address": "1 Weekly Street",
                "customer_phone": "07000000000",
                "special_instructions": "Ring bell",
                "items": [
                    {"product_id": self.product.id, "quantity": 2, "delivery_time": "12:30"},
                ],
            },
        )

        order = generate_order_from_recurring(recurring_order)

        self.assertEqual(order.customer, self.customer)
        self.assertEqual(order.total_amount, Decimal("8.00"))
        self.assertEqual(order.commission_total, Decimal("0.40"))
        suborder = order.producer_suborders.get()
        self.assertEqual(suborder.subtotal, Decimal("8.00"))
        self.assertEqual(suborder.commission_amount, Decimal("0.40"))
        self.assertEqual(suborder.producer_payout_amount, Decimal("7.60"))
        self.assertEqual(OrderItem.objects.get(suborder=suborder).quantity, 2)

        recurring_order.refresh_from_db()
        self.assertEqual(recurring_order.next_run_date.isoformat(), "2026-04-06")
        self.assertEqual(recurring_order.next_instance_overrides, {})
