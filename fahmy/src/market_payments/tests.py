from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from market_alerts.models import Notification
from market_orders.models import Order as MarketOrder
from market_orders.models import OrderItem as MarketOrderItem
from market_orders.models import ProducerSubOrder
from market_payments.models import Cart, CartItem, Order as PaymentOrder, OrderItem as PaymentOrderItem, Payment
from market_payments.services import calculate_commission_breakdown, request_checkout_breakdown
from market_products.models import FavouriteRecipe, Product, Recipe


class SingleProducerCheckoutTestCase(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.customer = self.user_model.objects.create_user(
            username="customer_tc007",
            password="secret123",
            role=self.user_model.Role.CUSTOMER,
            address="1 Test Street, Bristol",
            phone="07000111222",
        )
        self.producer = self.user_model.objects.create_user(
            username="producer_tc007",
            password="secret123",
            role=self.user_model.Role.PRODUCER,
            business_name="Bristol Valley Farm",
        )
        self.product_one = Product.objects.create(
            name="Heritage Carrots",
            producer=self.producer,
            price=Decimal("12.00"),
            stock_quantity=20,
            is_active=True,
        )
        self.product_two = Product.objects.create(
            name="Spring Greens",
            producer=self.producer,
            price=Decimal("8.00"),
            stock_quantity=20,
            is_active=True,
        )
        self.recipe = Recipe.objects.create(
            producer=self.producer,
            title="Spring Greens Tart",
            description="A simple tart for brunch and lunch tables.",
            ingredients="Greens\nPastry\nCheese",
            instructions="Bake until golden.",
            status=Recipe.Status.PUBLISHED,
            moderation_status=Recipe.ModerationStatus.APPROVED,
        )
        self.recipe.products.set([self.product_one, self.product_two])
        FavouriteRecipe.objects.create(customer=self.customer, recipe=self.recipe)

    def test_customer_cannot_add_out_of_stock_product_to_cart(self):
        self.client.force_login(self.customer)
        self.product_one.stock_quantity = 0
        self.product_one.save(update_fields=["stock_quantity"])

        response = self.client.post(
            reverse("market_payments:add_to_cart", args=[self.product_one.id]),
            {"next": reverse("market_payments:cart")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "currently out of stock")
        self.assertFalse(CartItem.objects.filter(product=self.product_one).exists())

    def test_customer_cannot_add_out_of_season_product_to_cart(self):
        self.client.force_login(self.customer)
        self.product_one.seasonal_availability = "seasonal"
        self.product_one.season_start_month = 6
        self.product_one.season_end_month = 8
        self.product_one.save(update_fields=["seasonal_availability", "season_start_month", "season_end_month"])

        with patch("market_products.models.timezone.localdate", return_value=date(2026, 12, 1)):
            response = self.client.post(
                reverse("market_payments:add_to_cart", args=[self.product_one.id]),
                {"next": reverse("market_payments:cart")},
                follow=True,
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "out of season")
        self.assertContains(response, "Available: June - August")
        self.assertFalse(CartItem.objects.filter(product=self.product_one).exists())

    def test_customer_cannot_add_more_than_available_stock_to_cart(self):
        self.client.force_login(self.customer)
        self.product_one.stock_quantity = 1
        self.product_one.save(update_fields=["stock_quantity"])

        self.client.post(
            reverse("market_payments:add_to_cart", args=[self.product_one.id]),
            {"next": reverse("market_payments:cart")},
        )
        response = self.client.post(
            reverse("market_payments:add_to_cart", args=[self.product_one.id]),
            {"next": reverse("market_payments:cart")},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only 1 unit(s)")
        cart_item = CartItem.objects.get(product=self.product_one)
        self.assertEqual(cart_item.quantity, 1)

    def test_tc007_customer_can_checkout_single_producer_order(self):
        self.client.force_login(self.customer)
        self.product_two.low_stock_threshold = 19
        self.product_two.save(update_fields=["low_stock_threshold"])

        add_to_cart_url = reverse("market_payments:add_to_cart", args=[self.product_one.id])
        self.client.post(add_to_cart_url, {"next": reverse("market_payments:cart")})
        self.client.post(add_to_cart_url, {"next": reverse("market_payments:cart")})
        self.client.post(
            reverse("market_payments:add_to_cart", args=[self.product_two.id]),
            {"next": reverse("market_payments:cart")},
        )

        cart_response = self.client.get(reverse("market_payments:cart"))
        self.assertEqual(cart_response.status_code, 200)
        self.assertContains(cart_response, "Heritage Carrots")
        self.assertContains(cart_response, "Spring Greens")
        self.assertContains(cart_response, "Platform commission (5%)")
        self.assertContains(cart_response, "Saved favourites")
        self.assertContains(cart_response, "Spring Greens Tart")

        payment_response = self.client.get(reverse("market_payments:payment"))
        self.assertEqual(payment_response.status_code, 200)
        self.assertContains(payment_response, "Bristol Valley Farm")
        self.assertContains(payment_response, "1 Test Street, Bristol")
        self.assertContains(payment_response, "Delivery date")
        self.assertContains(payment_response, "Spring Greens Tart")

        delivery_date = timezone.localtime(timezone.now() + timedelta(hours=49)).strftime("%Y-%m-%dT%H:%M")
        checkout_response = self.client.post(
            reverse("market_payments:pay_now"),
            {
                "delivery_address": "1 Test Street, Bristol",
                "customer_phone": "07000111222",
                "delivery_date": delivery_date,
                "payment_method": "visa_debit",
                "card_number": "4242 4242 4242 4242",
                "card_expiry": "12/34",
            },
            follow=True,
        )

        self.assertEqual(checkout_response.status_code, 200)
        self.assertContains(checkout_response, "Receipt")
        self.assertContains(checkout_response, "Order #")
        self.assertContains(checkout_response, "Spring Greens Tart")

        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.provider, "visa_debit")
        self.assertEqual(payment.subtotal, Decimal("32.00"))
        self.assertEqual(payment.commission_amount, Decimal("1.60"))
        self.assertEqual(payment.producer_payout_amount, Decimal("30.40"))
        self.assertEqual(payment.transaction_reference, f"demo-{payment.order_id}")

        market_order = MarketOrder.objects.get(customer=self.customer)
        self.assertEqual(market_order.status, MarketOrder.Status.CREATED)
        self.assertEqual(market_order.delivery_address, "1 Test Street, Bristol")
        self.assertEqual(market_order.customer_phone, "07000111222")
        self.assertEqual(market_order.total_amount, Decimal("32.00"))
        self.assertEqual(market_order.commission_total, Decimal("1.60"))

        suborder = ProducerSubOrder.objects.get(order=market_order)
        self.assertEqual(suborder.producer, self.producer)
        self.assertEqual(suborder.status, ProducerSubOrder.Status.PENDING)
        self.assertEqual(suborder.subtotal, Decimal("32.00"))
        self.assertEqual(suborder.commission_amount, Decimal("1.60"))
        self.assertEqual(suborder.producer_payout_amount, Decimal("30.40"))
        self.assertGreaterEqual(
            suborder.delivery_date,
            timezone.now() + timedelta(hours=48) - timedelta(minutes=1),
        )

        notification = Notification.objects.get(user=self.producer, type=Notification.Type.NEW_ORDER)
        self.assertIn(str(market_order.id), notification.message)

        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 0)

        self.product_one.refresh_from_db()
        self.product_two.refresh_from_db()
        self.assertEqual(self.product_one.stock_quantity, 18)
        self.assertEqual(self.product_two.stock_quantity, 19)
        low_stock_alert = Notification.objects.get(
            user=self.producer,
            product=self.product_two,
            type=Notification.Type.LOW_STOCK,
        )
        self.assertIn("Only 19 unit(s) remaining", low_stock_alert.message)
        self.assertFalse(low_stock_alert.is_resolved)

        self.client.force_login(self.producer)
        producer_dashboard = self.client.get("/orders/producer/dashboard/")
        self.assertEqual(producer_dashboard.status_code, 200)
        self.assertContains(producer_dashboard, self.customer.username)
        self.assertContains(producer_dashboard, "Heritage Carrots")

    def test_cart_quantity_update_does_not_show_noisy_success_message(self):
        self.client.force_login(self.customer)
        cart = Cart.objects.create(user=self.customer)
        item = CartItem.objects.create(cart=cart, product=self.product_one, quantity=1)

        response = self.client.post(
            reverse("market_payments:update_cart_item", args=[item.id]),
            {"quantity": "2"},
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Updated Heritage Carrots quantity to 2.")
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)


class MultiProducerCheckoutTestCase(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.customer = self.user_model.objects.create_user(
            username="customer_tc008",
            password="secret123",
            role=self.user_model.Role.CUSTOMER,
            address="22 Multi Street, Bristol",
            phone="07000999888",
        )
        self.bristol_valley = self.user_model.objects.create_user(
            username="producer_bristol",
            password="secret123",
            role=self.user_model.Role.PRODUCER,
            business_name="Bristol Valley Farm",
        )
        self.hillside_dairy = self.user_model.objects.create_user(
            username="producer_hillside",
            password="secret123",
            role=self.user_model.Role.PRODUCER,
            business_name="Hillside Dairy",
        )
        self.bristol_products = [
            Product.objects.create(
                name="Bristol Apples",
                producer=self.bristol_valley,
                price=Decimal("10.00"),
                stock_quantity=20,
                is_active=True,
            ),
            Product.objects.create(
                name="Bristol Kale",
                producer=self.bristol_valley,
                price=Decimal("8.00"),
                stock_quantity=20,
                is_active=True,
            ),
        ]
        self.hillside_products = [
            Product.objects.create(
                name="Hillside Milk",
                producer=self.hillside_dairy,
                price=Decimal("7.00"),
                stock_quantity=20,
                is_active=True,
            ),
            Product.objects.create(
                name="Hillside Cheese",
                producer=self.hillside_dairy,
                price=Decimal("15.00"),
                stock_quantity=20,
                is_active=True,
            ),
        ]

    def test_tc008_customer_can_checkout_multi_producer_order(self):
        self.client.force_login(self.customer)

        for product in self.bristol_products + self.hillside_products:
            self.client.post(
                reverse("market_payments:add_to_cart", args=[product.id]),
                {"next": reverse("market_payments:cart")},
            )

        cart_response = self.client.get(reverse("market_payments:cart"))
        self.assertEqual(cart_response.status_code, 200)
        self.assertContains(cart_response, "Bristol Valley Farm")
        self.assertContains(cart_response, "Hillside Dairy")
        self.assertContains(cart_response, "Grouped by producer")
        self.assertContains(cart_response, "Proceed to Checkout")

        payment_response = self.client.get(reverse("market_payments:payment"))
        self.assertEqual(payment_response.status_code, 200)
        self.assertContains(payment_response, "Separate producer section")
        self.assertContains(payment_response, "Multiple producers detected")
        self.assertContains(payment_response, "Delivery date for Bristol Valley Farm")
        self.assertContains(payment_response, "Delivery date for Hillside Dairy")
        self.assertContains(payment_response, "Platform commission (5%)")

        bristol_delivery = timezone.localtime(timezone.now() + timedelta(hours=49)).strftime("%Y-%m-%dT%H:%M")
        hillside_delivery = timezone.localtime(timezone.now() + timedelta(hours=73)).strftime("%Y-%m-%dT%H:%M")

        checkout_response = self.client.post(
            reverse("market_payments:pay_now"),
            {
                "delivery_address": "22 Multi Street, Bristol",
                "customer_phone": "07000999888",
                f"delivery_date_{self.bristol_valley.id}": bristol_delivery,
                f"delivery_date_{self.hillside_dairy.id}": hillside_delivery,
                "payment_method": "visa_debit",
                "card_number": "4242 4242 4242 4242",
                "card_expiry": "12/34",
            },
            follow=True,
        )

        self.assertEqual(checkout_response.status_code, 200)
        self.assertContains(checkout_response, "Receipt")
        self.assertContains(checkout_response, "Producer breakdown")
        self.assertContains(checkout_response, "Bristol Valley Farm")
        self.assertContains(checkout_response, "Hillside Dairy")

        payment = Payment.objects.get()
        self.assertEqual(payment.status, Payment.Status.PAID)
        self.assertEqual(payment.provider, "visa_debit")
        self.assertEqual(payment.subtotal, Decimal("40.00"))
        self.assertEqual(payment.commission_amount, Decimal("2.00"))
        self.assertEqual(payment.producer_payout_amount, Decimal("38.00"))

        market_order = MarketOrder.objects.get(customer=self.customer)
        self.assertEqual(market_order.status, MarketOrder.Status.CREATED)
        self.assertEqual(market_order.total_amount, Decimal("40.00"))
        self.assertEqual(market_order.commission_total, Decimal("2.00"))

        suborders = list(
            market_order.producer_suborders.select_related("producer").prefetch_related("items").order_by("producer__business_name")
        )
        self.assertEqual(len(suborders), 2)

        bristol_suborder = next(sub for sub in suborders if sub.producer_id == self.bristol_valley.id)
        hillside_suborder = next(sub for sub in suborders if sub.producer_id == self.hillside_dairy.id)

        self.assertEqual(bristol_suborder.subtotal, Decimal("18.00"))
        self.assertEqual(bristol_suborder.commission_amount, Decimal("0.90"))
        self.assertEqual(bristol_suborder.producer_payout_amount, Decimal("17.10"))
        self.assertEqual(bristol_suborder.items.count(), 2)

        self.assertEqual(hillside_suborder.subtotal, Decimal("22.00"))
        self.assertEqual(hillside_suborder.commission_amount, Decimal("1.10"))
        self.assertEqual(hillside_suborder.producer_payout_amount, Decimal("20.90"))
        self.assertEqual(hillside_suborder.items.count(), 2)

        self.assertNotEqual(
            bristol_suborder.delivery_date.replace(second=0, microsecond=0),
            hillside_suborder.delivery_date.replace(second=0, microsecond=0),
        )

        notifications = Notification.objects.filter(type=Notification.Type.NEW_ORDER).order_by("user__business_name")
        self.assertEqual(notifications.count(), 2)
        self.assertEqual({n.user_id for n in notifications}, {self.bristol_valley.id, self.hillside_dairy.id})
        self.assertEqual(notifications[0].product.producer_id, notifications[0].user_id)
        self.assertEqual(notifications[1].product.producer_id, notifications[1].user_id)

        cart = Cart.objects.get(user=self.customer)
        self.assertEqual(cart.items.count(), 0)

        for product in self.bristol_products + self.hillside_products:
            product.refresh_from_db()
            self.assertEqual(product.stock_quantity, 19)

        self.client.force_login(self.bristol_valley)
        producer_one_dashboard = self.client.get("/orders/producer/dashboard/")
        self.assertEqual(producer_one_dashboard.status_code, 200)
        self.assertContains(producer_one_dashboard, "Bristol Apples")
        self.assertContains(producer_one_dashboard, "Bristol Kale")
        self.assertNotContains(producer_one_dashboard, "Hillside Milk")
        self.assertNotContains(producer_one_dashboard, "Hillside Cheese")

        self.client.force_login(self.hillside_dairy)
        producer_two_dashboard = self.client.get("/orders/producer/dashboard/")
        self.assertEqual(producer_two_dashboard.status_code, 200)
        self.assertContains(producer_two_dashboard, "Hillside Milk")
        self.assertContains(producer_two_dashboard, "Hillside Cheese")
        self.assertNotContains(producer_two_dashboard, "Bristol Apples")
        self.assertNotContains(producer_two_dashboard, "Bristol Kale")


class PaymentCalculationServiceTests(TestCase):
    def test_commission_breakdown_is_rounded_to_two_decimal_places(self):
        breakdown = calculate_commission_breakdown(Decimal("150.00"))
        self.assertEqual(
            breakdown,
            {
                "subtotal": Decimal("150.00"),
                "commission_amount": Decimal("7.50"),
                "producer_payout_amount": Decimal("142.50"),
            },
        )

    def test_checkout_breakdown_falls_back_safely_when_api_is_unavailable(self):
        breakdown = request_checkout_breakdown(
            Decimal("150.00"),
            {
                10: Decimal("80.00"),
                11: Decimal("70.00"),
            },
        )
        self.assertEqual(breakdown["subtotal"], Decimal("150.00"))
        self.assertEqual(breakdown["commission_amount"], Decimal("7.50"))
        self.assertEqual(breakdown["producer_payout_amount"], Decimal("142.50"))
        self.assertEqual(
            breakdown["producer_breakdown"],
            [
                {
                    "producer_id": 10,
                    "subtotal": Decimal("80.00"),
                    "commission_amount": Decimal("4.00"),
                    "producer_payout_amount": Decimal("76.00"),
                },
                {
                    "producer_id": 11,
                    "subtotal": Decimal("70.00"),
                    "commission_amount": Decimal("3.50"),
                    "producer_payout_amount": Decimal("66.50"),
                },
            ],
        )


class OrderHistoryAndReorderTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.customer = self.user_model.objects.create_user(
            username="history_customer",
            email="history_customer@test.com",
            password="secret123",
            role=self.user_model.Role.CUSTOMER,
            address="7 History Road, Bristol",
            phone="07000111000",
        )
        self.other_customer = self.user_model.objects.create_user(
            username="other_customer",
            email="other_customer@test.com",
            password="secret123",
            role=self.user_model.Role.CUSTOMER,
        )
        self.producer = self.user_model.objects.create_user(
            username="history_producer",
            email="history_producer@test.com",
            password="secret123",
            role=self.user_model.Role.PRODUCER,
            business_name="History Farm",
        )
        self.product_one = Product.objects.create(
            name="History Apples",
            producer=self.producer,
            price=Decimal("6.00"),
            stock_quantity=10,
            is_active=True,
        )
        self.product_two = Product.objects.create(
            name="History Pears",
            producer=self.producer,
            price=Decimal("4.00"),
            stock_quantity=6,
            is_active=True,
        )
        self.product_three = Product.objects.create(
            name="History Plums",
            producer=self.producer,
            price=Decimal("6.00"),
            stock_quantity=8,
            is_active=True,
        )

    def _create_previous_order(self, *, customer, product_specs):
        subtotal = sum(product.price * quantity for product, quantity in product_specs)
        subtotal = subtotal.quantize(Decimal("0.01"))
        commission = (subtotal * Decimal("0.05")).quantize(Decimal("0.01"))
        total = (subtotal + commission).quantize(Decimal("0.01"))

        payment_order = PaymentOrder.objects.create(
            user=customer,
            subtotal=subtotal,
            commission=commission,
            total=total,
        )
        Payment.objects.create(
            order=payment_order,
            status=Payment.Status.PAID,
            provider="visa_debit",
        )

        market_order = MarketOrder.objects.create(
            customer=customer,
            status=MarketOrder.Status.CREATED,
            total_amount=subtotal,
            commission_total=commission,
            delivery_address=getattr(customer, "address", "") or "Test address",
            customer_phone=getattr(customer, "phone", "") or "07000000000",
        )
        suborder = ProducerSubOrder.objects.create(
            order=market_order,
            producer=self.producer,
            status=ProducerSubOrder.Status.PENDING,
            delivery_date=timezone.now() + timedelta(days=3),
            subtotal=subtotal,
            commission_amount=commission,
            producer_payout_amount=subtotal - commission,
        )

        for product, quantity in product_specs:
            PaymentOrderItem.objects.create(
                order=payment_order,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
            )
            MarketOrderItem.objects.create(
                suborder=suborder,
                product=product,
                product_name=product.name,
                unit_price=product.price,
                quantity=quantity,
            )

        return payment_order

    def test_customer_can_view_own_order_history(self):
        own_order = self._create_previous_order(
            customer=self.customer,
            product_specs=[(self.product_one, 2)],
        )
        other_order = self._create_previous_order(
            customer=self.other_customer,
            product_specs=[(self.product_two, 1)],
        )

        self.client.force_login(self.customer)
        response = self.client.get(reverse("market_payments:order_history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"Order #{own_order.id}")
        self.assertContains(response, "History Apples")
        self.assertNotContains(response, f"Order #{other_order.id}")

    def test_logged_out_user_is_redirected_from_order_history(self):
        response = self.client.get(reverse("market_payments:order_history"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_producer_is_forbidden_from_order_history(self):
        self.client.force_login(self.producer)
        response = self.client.get(reverse("market_payments:order_history"))
        self.assertEqual(response.status_code, 403)

    def test_customer_can_reorder_own_previous_order(self):
        order = self._create_previous_order(
            customer=self.customer,
            product_specs=[(self.product_one, 2), (self.product_two, 1)],
        )

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("market_payments:reorder_order", args=[order.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reordered 3 item(s) into your cart.")

        cart = Cart.objects.get(user=self.customer)
        apples = CartItem.objects.get(cart=cart, product=self.product_one)
        pears = CartItem.objects.get(cart=cart, product=self.product_two)
        self.assertEqual(apples.quantity, 2)
        self.assertEqual(pears.quantity, 1)

    def test_customer_cannot_reorder_another_customers_order(self):
        order = self._create_previous_order(
            customer=self.other_customer,
            product_specs=[(self.product_one, 1)],
        )

        self.client.force_login(self.customer)
        response = self.client.post(reverse("market_payments:reorder_order", args=[order.id]))
        self.assertEqual(response.status_code, 404)

    def test_reorder_matches_correct_previous_order_when_totals_repeat(self):
        first_order = self._create_previous_order(
            customer=self.customer,
            product_specs=[(self.product_one, 1)],
        )
        self._create_previous_order(
            customer=self.customer,
            product_specs=[(self.product_three, 1)],
        )

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("market_payments:reorder_order", args=[first_order.id]),
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        cart = Cart.objects.get(user=self.customer)
        self.assertTrue(CartItem.objects.filter(cart=cart, product=self.product_one, quantity=1).exists())
        self.assertFalse(CartItem.objects.filter(cart=cart, product=self.product_three).exists())

    def test_logged_out_user_is_redirected_from_reorder(self):
        order = self._create_previous_order(
            customer=self.customer,
            product_specs=[(self.product_one, 1)],
        )

        response = self.client.post(reverse("market_payments:reorder_order", args=[order.id]))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_producer_is_forbidden_from_reorder(self):
        order = self._create_previous_order(
            customer=self.customer,
            product_specs=[(self.product_one, 1)],
        )

        self.client.force_login(self.producer)
        response = self.client.post(reverse("market_payments:reorder_order", args=[order.id]))
        self.assertEqual(response.status_code, 403)
