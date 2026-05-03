from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.core.management import call_command
from django.core.exceptions import ValidationError
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
    build_finance_pdf,
    calculate_running_period_summaries,
    generate_due_recurring_orders,
    generate_order_from_recurring,
    generate_weekly_settlements,
    get_settlement_suborders,
    settlement_week_bounds,
    update_recurring_order_status,
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
        self.assertEqual(list(settlement.included_suborders.all()), [self.suborder])

    def test_get_settlement_suborders_excludes_suborders_already_linked_elsewhere(self):
        other_admin = get_user_model().objects.create_user(
            username="finance_admin_3",
            password="secret123",
            role=get_user_model().Role.ADMIN,
        )
        settlement = generate_weekly_settlements(
            actor=other_admin,
            producer=self.producer,
            week_start=settlement_week_bounds(self.suborder.delivery_date)[0],
        )[0]

        eligible = get_settlement_suborders(
            producer_id=self.producer.id,
            week_start=settlement.week_start,
        )
        self.assertEqual(eligible, [])


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

    def test_inactive_or_cancelled_recurring_orders_cannot_generate(self):
        recurring_order = RecurringOrder.objects.create(
            customer=self.customer,
            status=RecurringOrder.Status.PAUSED,
            active=False,
            next_run_date=date(2026, 3, 30),
            template_order_data={
                "items": [
                    {"product_id": self.product.id, "quantity": 1, "delivery_time": "12:30"},
                ],
            },
        )
        with self.assertRaisesMessage(ValidationError, "not active"):
            generate_order_from_recurring(recurring_order)

        update_recurring_order_status(recurring_order, RecurringOrder.Status.CANCELLED)
        with self.assertRaisesMessage(ValidationError, "not active"):
            generate_order_from_recurring(recurring_order)

    def test_generate_due_recurring_orders_only_runs_active_due_templates(self):
        active_due = RecurringOrder.objects.create(
            customer=self.customer,
            next_run_date=timezone.localdate(),
            template_order_data={
                "items": [{"product_id": self.product.id, "quantity": 1, "delivery_time": "12:30"}],
            },
        )
        paused_due = RecurringOrder.objects.create(
            customer=self.customer,
            status=RecurringOrder.Status.PAUSED,
            active=False,
            next_run_date=timezone.localdate(),
            template_order_data={
                "items": [{"product_id": self.product.id, "quantity": 1, "delivery_time": "13:00"}],
            },
        )

        orders = generate_due_recurring_orders(run_date=timezone.localdate())
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0].customer, self.customer)

        active_due.refresh_from_db()
        paused_due.refresh_from_db()
        self.assertGreater(active_due.last_run_at, timezone.now() - timedelta(minutes=1))
        self.assertIsNone(paused_due.last_run_at)

    def test_management_command_generates_due_orders(self):
        RecurringOrder.objects.create(
            customer=self.customer,
            next_run_date=timezone.localdate(),
            template_order_data={
                "items": [{"product_id": self.product.id, "quantity": 1, "delivery_time": "12:30"}],
            },
        )
        call_command("run_due_recurring_orders", run_date=timezone.localdate().isoformat())
        self.assertEqual(Order.objects.count(), 1)

    def test_unavailable_product_blocks_generation(self):
        recurring_order = RecurringOrder.objects.create(
            customer=self.customer,
            next_run_date=date(2026, 3, 30),
            template_order_data={
                "items": [{"product_id": self.product.id, "quantity": 99, "delivery_time": "12:30"}],
            },
        )
        with self.assertRaisesMessage(ValidationError, "Not enough stock"):
            generate_order_from_recurring(recurring_order)


class RecurringOrderViewTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.customer = user_model.objects.create_user(
            username="recurring_view_customer",
            password="secret123",
            role=user_model.Role.CUSTOMER,
            address="2 Recur Street",
            phone="07000000001",
        )
        self.other_customer = user_model.objects.create_user(
            username="recurring_other_customer",
            password="secret123",
            role=user_model.Role.CUSTOMER,
        )
        self.producer = user_model.objects.create_user(
            username="recurring_view_producer",
            password="secret123",
            role=user_model.Role.PRODUCER,
            business_name="Repeat Farm",
        )
        self.product = Product.objects.create(
            name="Bread Crate",
            producer=self.producer,
            price=Decimal("5.00"),
            stock_quantity=20,
            is_active=True,
        )
        self.order = Order.objects.create(
            customer=self.customer,
            total_amount=Decimal("10.00"),
            commission_total=Decimal("0.50"),
            delivery_address="2 Recur Street",
            customer_phone="07000000001",
            special_instructions="Leave at hatch",
        )
        self.suborder = ProducerSubOrder.objects.create(
            order=self.order,
            producer=self.producer,
            status=ProducerSubOrder.Status.DELIVERED,
            delivery_date=timezone.now(),
            subtotal=Decimal("10.00"),
            commission_amount=Decimal("0.50"),
            producer_payout_amount=Decimal("9.50"),
        )
        OrderItem.objects.create(
            suborder=self.suborder,
            product=self.product,
            product_name=self.product.name,
            unit_price=self.product.price,
            quantity=2,
        )
        self.recurring_order = RecurringOrder.objects.create(
            customer=self.customer,
            next_run_date=timezone.localdate(),
            template_order_data={
                "delivery_address": "2 Recur Street",
                "customer_phone": "07000000001",
                "special_instructions": "Leave at hatch",
                "items": [{"product_id": self.product.id, "quantity": 2, "delivery_time": "12:30"}],
            },
        )

    def test_customer_can_create_recurring_order_from_existing_order(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("market_finance:create_recurring_order_from_order", args=[self.order.id]),
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "weekly repeat order")
        self.assertEqual(RecurringOrder.objects.filter(customer=self.customer).count(), 2)

    def test_recurring_order_dashboard_only_shows_customer_records(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse("market_finance:recurring_orders_dashboard"))
        self.assertContains(response, "Weekly Order")
        self.assertContains(response, "Change Day and Time")
        self.assertContains(response, "Estimated weekly total")

        self.client.force_login(self.other_customer)
        response = self.client.get(reverse("market_finance:recurring_orders_dashboard"))
        self.assertNotContains(response, f"Weekly Order #{self.recurring_order.id}")

    def test_customer_cannot_update_another_customers_recurring_order(self):
        self.client.force_login(self.other_customer)
        response = self.client.post(
            reverse("market_finance:change_recurring_order_status", args=[self.recurring_order.id]),
            {"status": "PAUSED"},
        )
        self.assertEqual(response.status_code, 404)

    def test_pause_reactivate_and_cancel_flow(self):
        self.client.force_login(self.customer)

        pause_response = self.client.post(
            reverse("market_finance:change_recurring_order_status", args=[self.recurring_order.id]),
            {"status": "PAUSED"},
            follow=True,
        )
        self.assertContains(pause_response, "paused")
        self.recurring_order.refresh_from_db()
        self.assertEqual(self.recurring_order.status, RecurringOrder.Status.PAUSED)
        self.assertFalse(self.recurring_order.active)

        reactivate_response = self.client.post(
            reverse("market_finance:change_recurring_order_status", args=[self.recurring_order.id]),
            {"status": "ACTIVE"},
            follow=True,
        )
        self.assertContains(reactivate_response, "resumed")
        self.recurring_order.refresh_from_db()
        self.assertEqual(self.recurring_order.status, RecurringOrder.Status.ACTIVE)
        self.assertTrue(self.recurring_order.active)

        cancel_response = self.client.post(
            reverse("market_finance:change_recurring_order_status", args=[self.recurring_order.id]),
            {"status": "CANCELLED"},
            follow=True,
        )
        self.assertContains(cancel_response, "cancelled")
        self.recurring_order.refresh_from_db()
        self.assertEqual(self.recurring_order.status, RecurringOrder.Status.CANCELLED)

    def test_customer_can_change_repeat_order_day_and_time(self):
        self.client.force_login(self.customer)
        delivery_day = timezone.localdate() + timedelta(days=3)
        response = self.client.post(
            reverse("market_finance:update_recurring_order_time", args=[self.recurring_order.id]),
            {"next_run_date": delivery_day.isoformat(), "preferred_delivery_time": "15:45"},
            follow=True,
        )
        self.assertContains(response, "delivery day and time have been updated")
        self.recurring_order.refresh_from_db()
        self.assertEqual(self.recurring_order.next_run_date, delivery_day)
        self.assertEqual(self.recurring_order.preferred_delivery_time.strftime("%H:%M"), "15:45")
        self.assertEqual(self.recurring_order.template_order_data["preferred_delivery_time"], "15:45")
        self.assertEqual(self.recurring_order.template_order_data["items"][0]["delivery_time"], "15:45")

    def test_customer_cannot_set_repeat_order_inside_48_hour_lead_time(self):
        self.client.force_login(self.customer)
        original_date = self.recurring_order.next_run_date
        response = self.client.post(
            reverse("market_finance:update_recurring_order_time", args=[self.recurring_order.id]),
            {"next_run_date": timezone.localdate().isoformat(), "preferred_delivery_time": "23:59"},
            follow=True,
        )
        messages = [str(message) for message in get_messages(response.wsgi_request)]
        self.assertIn("Please choose a delivery day and time at least 48 hours from now.", messages)
        self.recurring_order.refresh_from_db()
        self.assertEqual(self.recurring_order.next_run_date, original_date)

    def test_customer_can_edit_repeat_order_details(self):
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("market_finance:update_recurring_order_details", args=[self.recurring_order.id]),
            {
                "delivery_address": "99 Updated Street",
                "customer_phone": "07123456789",
                "special_instructions": "Ring when outside",
                f"quantity_{self.product.id}": "4",
            },
            follow=True,
        )
        self.assertContains(response, "repeat order has been updated")
        self.recurring_order.refresh_from_db()
        self.assertEqual(self.recurring_order.template_order_data["delivery_address"], "99 Updated Street")
        self.assertEqual(self.recurring_order.template_order_data["customer_phone"], "07123456789")
        self.assertEqual(self.recurring_order.template_order_data["special_instructions"], "Ring when outside")
        self.assertEqual(self.recurring_order.template_order_data["items"][0]["quantity"], 4)

    def test_due_run_endpoint_generates_only_current_users_due_orders(self):
        RecurringOrder.objects.create(
            customer=self.other_customer,
            next_run_date=timezone.localdate(),
            template_order_data={
                "items": [{"product_id": self.product.id, "quantity": 1, "delivery_time": "12:30"}],
            },
        )
        self.client.force_login(self.customer)
        response = self.client.post(reverse("market_finance:run_due_recurring_orders_now"), follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "due repeat orders have been placed")
        self.assertEqual(Order.objects.filter(customer=self.customer).count(), 2)
        self.assertEqual(Order.objects.filter(customer=self.other_customer).count(), 0)


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
        self.assertContains(response, "Admin Finance Overview")
        self.assertContains(response, "Monthly Summary")
        self.assertContains(response, "Year To Date")
        self.assertContains(response, "£7.50")

    def test_admin_finance_nav_does_not_show_customer_buying_links(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("market_finance:admin_finance_dashboard"))

        self.assertNotContains(response, 'class="siteNav"')
        self.assertContains(response, ">Overview<")
        self.assertContains(response, ">Financial Reports<")
        self.assertContains(response, ">Settlements<")
        self.assertContains(response, ">Exports<")
        self.assertContains(response, ">Django Admin<")
        self.assertNotContains(response, 'href="/discover/"')
        self.assertNotContains(response, 'href="/payments/cart/"')
        self.assertNotContains(response, 'href="/payments/orders/"')
        self.assertNotContains(response, 'href="/finance/recurring/"')

    def test_customer_nav_keeps_marketplace_buying_links(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse("discovery_alt"))

        self.assertContains(response, ">Discover<")
        self.assertContains(response, ">Cart<")
        self.assertContains(response, ">Orders<")
        self.assertContains(response, ">Repeat Orders<")
        self.assertNotContains(response, ">Finance<")
        self.assertNotContains(response, ">Admin<")

    def test_admin_dashboard_reports_settlements_exports_and_order_detail_render(self):
        self.client.force_login(self.admin)

        reports_response = self.client.get(reverse("market_finance:admin_finance_reports"))
        self.assertContains(reports_response, "Financial Reports")
        self.assertContains(reports_response, "Commission Report")
        self.assertContains(reports_response, "Order Breakdown")
        self.assertContains(reports_response, "Previous 2 weeks")
        self.assertContains(reports_response, "5% commission amount")
        self.assertContains(reports_response, "View order detail")

        settlements_response = self.client.get(reverse("market_finance:admin_finance_settlements"))
        self.assertContains(settlements_response, "Settlement Monitoring")

        exports_page = self.client.get(reverse("market_finance:admin_finance_exports"))
        self.assertContains(exports_page, "Exports & Downloads")
        self.assertContains(exports_page, "Download CSV")

        order_detail = self.client.get(
            reverse("market_finance:admin_order_finance_detail", args=[self.order.id])
        )
        self.assertContains(order_detail, "Producer payout (95%)")
        self.assertContains(order_detail, "£76.00")
        self.assertContains(order_detail, "£66.50")

        csv_response = self.client.get(reverse("market_finance:export_admin_finance_csv"))
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("suborder_id,order_id,producer,customer", csv_response.content.decode("utf-8"))
        self.assertIn("gross_sales,150.00", csv_response.content.decode("utf-8"))

        excel_response = self.client.get(reverse("market_finance:export_admin_finance_excel"))
        self.assertEqual(excel_response.status_code, 200)
        self.assertEqual(excel_response["Content-Type"], "application/vnd.ms-excel")

        pdf_response = self.client.get(reverse("market_finance:export_admin_finance_pdf"))
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")

    def test_non_admin_users_cannot_access_admin_finance_pages(self):
        self.client.force_login(self.producer_one)

        protected_urls = [
            reverse("market_finance:admin_finance_dashboard"),
            reverse("market_finance:admin_finance_reports"),
            reverse("market_finance:admin_finance_settlements"),
            reverse("market_finance:admin_finance_exports"),
            reverse("market_finance:admin_order_finance_detail", args=[self.order.id]),
            reverse("market_finance:export_admin_finance_csv"),
            reverse("market_finance:export_admin_finance_excel"),
            reverse("market_finance:export_admin_finance_pdf"),
        ]

        for url in protected_urls:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 403)

    def test_report_filters_limit_rows_and_totals(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("market_finance:admin_finance_reports"),
            {"producer": str(self.producer_one.id)},
        )
        self.assertContains(response, "£4.00")
        self.assertNotContains(response, "£3.50")

    def test_settlement_page_prefers_stored_records_and_shows_audit_source_rows(self):
        generate_weekly_settlements(
            actor=self.admin,
            producer=self.producer_one,
            week_start=settlement_week_bounds(self.suborder_one.delivery_date)[0],
        )
        self.client.force_login(self.admin)

        response = self.client.get(reverse("market_finance:admin_finance_settlements"))
        self.assertContains(response, "Stored")
        self.assertContains(response, "Contributing orders")
        self.assertContains(response, f"#{self.order.id}")

    def test_settlement_csv_export_is_limited_to_owner_for_producers(self):
        generate_weekly_settlements(
            actor=self.admin,
            producer=self.producer_one,
            week_start=settlement_week_bounds(self.suborder_one.delivery_date)[0],
        )
        self.client.force_login(self.producer_two)
        response = self.client.get(
            reverse("market_finance:export_settlement_csv"),
            {
                "producer_id": self.producer_one.id,
                "week_start": settlement_week_bounds(self.suborder_one.delivery_date)[0].isoformat(),
            },
        )
        self.assertEqual(response.status_code, 403)

    def test_settlement_csv_export_uses_stored_suborders(self):
        generate_weekly_settlements(
            actor=self.admin,
            producer=self.producer_one,
            week_start=settlement_week_bounds(self.suborder_one.delivery_date)[0],
        )
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("market_finance:export_settlement_csv"),
            {
                "producer_id": self.producer_one.id,
                "week_start": settlement_week_bounds(self.suborder_one.delivery_date)[0].isoformat(),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("settlement_status,Generated", response.content.decode("utf-8"))


class FinancePdfTests(TestCase):
    def test_pdf_builder_adds_text_leading_and_multiple_line_entries(self):
        pdf = build_finance_pdf(
            [
                "Admin Finance Report",
                "This is a deliberately long finance line that should wrap cleanly across the PDF output instead of collapsing onto a single unreadable row in the exported file.",
                "Another line",
            ]
        )

        self.assertIn(b"TL", pdf)
        self.assertIn(b"T*", pdf)
        self.assertTrue(pdf.startswith(b"%PDF-1.4"))
