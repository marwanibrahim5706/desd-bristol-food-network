from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from market_orders.models import Order, OrderItem, ProducerSubOrder
from market_payments.models import Order as PaymentOrder
from market_payments.models import Payment, Settlement
from market_products.models import Product

from .models import RecurringOrder
from .services import (
    build_order_finance_summaries,
    calculate_running_period_summaries,
    generate_order_from_recurring,
    generate_weekly_settlements,
    settlement_week_bounds,
)


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


class AdminFinanceDashboardTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.admin = user_model.objects.create_user(
            username="finance_admin_2",
            password="secret123",
            role=user_model.Role.ADMIN,
            is_staff=True,
        )
        self.customer = user_model.objects.create_user(
            username="finance_customer_2",
            password="secret123",
            role=user_model.Role.CUSTOMER,
        )
        self.producer_one = user_model.objects.create_user(
            username="producer_one",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Bristol Valley Farm",
        )
        self.producer_two = user_model.objects.create_user(
            username="producer_two",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Harbour Bakery",
        )

        self.order = Order.objects.create(
            customer=self.customer,
            status=Order.Status.CREATED,
            total_amount=Decimal("150.00"),
            commission_total=Decimal("7.50"),
            created_at=timezone.now(),
        )
        self.suborder_one = ProducerSubOrder.objects.create(
            order=self.order,
            producer=self.producer_one,
            status=ProducerSubOrder.Status.DELIVERED,
            delivery_date=timezone.now(),
            subtotal=Decimal("80.00"),
            commission_amount=Decimal("4.00"),
            producer_payout_amount=Decimal("76.00"),
        )
        self.suborder_two = ProducerSubOrder.objects.create(
            order=self.order,
            producer=self.producer_two,
            status=ProducerSubOrder.Status.DELIVERED,
            delivery_date=timezone.now(),
            subtotal=Decimal("70.00"),
            commission_amount=Decimal("3.50"),
            producer_payout_amount=Decimal("66.50"),
        )
        payment_order = PaymentOrder.objects.create(
            user=self.customer,
            subtotal=Decimal("150.00"),
            commission=Decimal("7.50"),
            total=Decimal("157.50"),
        )
        Payment.objects.create(
            order=payment_order,
            subtotal=Decimal("150.00"),
            commission_amount=Decimal("7.50"),
            producer_payout_amount=Decimal("142.50"),
            status=Payment.Status.PAID,
            provider="visa_debit",
        )

    def test_order_summary_calculations_match_multi_vendor_distribution(self):
        summaries = build_order_finance_summaries([self.suborder_one, self.suborder_two])
        self.assertEqual(len(summaries), 1)
        summary = summaries[0]
        self.assertEqual(summary["order_total"], Decimal("150.00"))
        self.assertEqual(summary["commission_total"], Decimal("7.50"))
        self.assertEqual(summary["producer_payout_total"], Decimal("142.50"))

    def test_running_period_summaries_include_month_and_ytd(self):
        summaries = calculate_running_period_summaries(reference=timezone.now())
        self.assertEqual(summaries["month"]["total_commission"], Decimal("7.50"))
        self.assertEqual(summaries["ytd"]["total_payouts"], Decimal("142.50"))

    def test_admin_dashboard_shows_order_summary_and_exports(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("market_finance:admin_finance_dashboard"))
        self.assertContains(response, "Order Summaries")
        self.assertContains(response, "Monthly Summary")
        self.assertContains(response, "Year To Date")
        self.assertContains(response, "£7.50")

        order_detail = self.client.get(
            reverse("market_finance:admin_order_finance_detail", args=[self.order.id])
        )
        self.assertContains(order_detail, "Producer payout (95%)")
        self.assertContains(order_detail, "£76.00")
        self.assertContains(order_detail, "£66.50")

        csv_response = self.client.get(reverse("market_finance:export_admin_finance_csv"))
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("suborder_id,order_id,producer,customer", csv_response.content.decode("utf-8"))

        excel_response = self.client.get(reverse("market_finance:export_admin_finance_excel"))
        self.assertEqual(excel_response.status_code, 200)
        self.assertEqual(excel_response["Content-Type"], "application/vnd.ms-excel")

        pdf_response = self.client.get(reverse("market_finance:export_admin_finance_pdf"))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
