from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from market_orders.models import Order as MarketOrder
from market_orders.models import ProducerSubOrder
from market_payments.models import Order as PaymentOrder
from market_payments.models import Payment


class RegistrationFlowTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user_model = get_user_model()

    def test_customer_registration_creates_customer_role_user(self):
        response = self.client.post(
            reverse("accounts:register_customer"),
            {
                "full_name": "Customer One",
                "email": "customer1@test.com",
                "phone": "07000111222",
                "address": "1 Test Street",
                "postcode": "BS1 1AA",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        user = self.user_model.objects.get(email="customer1@test.com")
        self.assertEqual(user.role, self.user_model.Role.CUSTOMER)
        self.assertEqual(user.first_name, "Customer One")

    def test_producer_registration_rejects_duplicate_email(self):
        self.user_model.objects.create_user(
            username="producer@test.com",
            email="producer@test.com",
            password="StrongPass123!",
            role=self.user_model.Role.PRODUCER,
        )

        response = self.client.post(
            reverse("accounts:register_producer"),
            {
                "business_name": "Existing Farm",
                "contact_name": "Owner",
                "email": "producer@test.com",
                "phone": "07000111222",
                "address": "Farm Road",
                "postcode": "BS1 1AA",
                "password": "StrongPass123!",
                "confirm_password": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "An account with this email already exists.")


class AuthenticationAuthorisationTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.customer = self.user_model.objects.create_user(
            username="customer@test.com",
            email="customer@test.com",
            password="StrongPass123!",
            role=self.user_model.Role.CUSTOMER,
            address="1 Test Street",
            phone="07000111222",
        )
        self.producer = self.user_model.objects.create_user(
            username="producer@test.com",
            email="producer@test.com",
            password="StrongPass123!",
            role=self.user_model.Role.PRODUCER,
            business_name="Test Farm",
        )
        self.other_producer = self.user_model.objects.create_user(
            username="other@test.com",
            email="other@test.com",
            password="StrongPass123!",
            role=self.user_model.Role.PRODUCER,
            business_name="Other Farm",
        )
        self.admin_user = self.user_model.objects.create_user(
            username="finance_admin@test.com",
            email="finance_admin@test.com",
            password="StrongPass123!",
            role=self.user_model.Role.ADMIN,
            is_staff=True,
        )

    def test_login_accepts_email_and_redirects_by_role(self):
        response = self.client.post(
            reverse("accounts:login"),
            {
                "identifier": "customer@test.com",
                "password": "StrongPass123!",
            },
        )

        self.assertRedirects(response, "/discover/")

    def test_login_without_remember_me_expires_on_browser_close(self):
        self.client.post(
            reverse("accounts:login"),
            {
                "identifier": "customer@test.com",
                "password": "StrongPass123!",
            },
        )

        self.assertEqual(self.client.session.get("_session_expiry"), 0)

    def test_login_with_remember_me_sets_two_week_session(self):
        self.client.post(
            reverse("accounts:login"),
            {
                "identifier": "customer@test.com",
                "password": "StrongPass123!",
                "remember_me": "1",
            },
        )

        self.assertEqual(self.client.session.get("_session_expiry"), 1209600)

    def test_login_ignores_unsafe_next_redirect(self):
        response = self.client.post(
            reverse("accounts:login"),
            {
                "identifier": "customer@test.com",
                "password": "StrongPass123!",
                "next": "https://malicious.example/steal",
            },
        )

        self.assertRedirects(response, "/discover/")

    def test_repeated_failed_logins_are_temporarily_limited(self):
        for _attempt in range(5):
            response = self.client.post(
                reverse("accounts:login"),
                {
                    "identifier": "customer@test.com",
                    "password": "WrongPass123!",
                },
            )
            self.assertContains(response, "Invalid credentials")

        response = self.client.post(
            reverse("accounts:login"),
            {
                "identifier": "customer@test.com",
                "password": "WrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "Too many failed sign-in attempts", status_code=429)

    def test_logged_out_user_is_redirected_from_producer_dashboard(self):
        response = self.client.get("/orders/producer/dashboard/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_customer_cannot_access_producer_dashboard(self):
        self.client.force_login(self.customer)
        response = self.client.get("/orders/producer/dashboard/")
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_access_producer_dashboard(self):
        self.client.force_login(self.admin_user)
        response = self.client.get("/orders/producer/dashboard/")
        self.assertEqual(response.status_code, 403)

    def test_producer_cannot_access_customer_checkout(self):
        self.client.force_login(self.producer)
        response = self.client.get(reverse("market_payments:cart"))
        self.assertEqual(response.status_code, 403)

    def test_admin_cannot_access_customer_checkout(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("market_payments:cart"))
        self.assertEqual(response.status_code, 403)

    def test_producer_suborder_detail_is_limited_to_owner(self):
        market_order = MarketOrder.objects.create(customer=self.customer)
        suborder = ProducerSubOrder.objects.create(
            order=market_order,
            producer=self.producer,
            delivery_date=timezone.now() + timedelta(days=3),
        )

        self.client.force_login(self.other_producer)
        response = self.client.get(f"/orders/producer/suborders/{suborder.id}/")
        self.assertEqual(response.status_code, 404)

    def test_receipt_is_limited_to_customer_owner(self):
        order = PaymentOrder.objects.create(
            user=self.customer,
            subtotal="10.00",
            commission="0.50",
            total="10.50",
        )
        payment = Payment.objects.create(order=order, status=Payment.Status.PAID, provider="demo")

        other_customer = self.user_model.objects.create_user(
            username="customer2@test.com",
            email="customer2@test.com",
            password="StrongPass123!",
            role=self.user_model.Role.CUSTOMER,
        )

        self.client.force_login(other_customer)
        response = self.client.get(reverse("market_payments:receipt", args=[payment.id]))
        self.assertEqual(response.status_code, 404)

    def test_logout_clears_authenticated_session(self):
        self.client.force_login(self.customer)

        logout_response = self.client.post(reverse("accounts:logout"), follow=True)
        self.assertEqual(logout_response.status_code, 200)

        response = self.client.get(reverse("market_payments:cart"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/", response["Location"])

    def test_stale_login_form_redirects_to_fresh_login_page(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.post(
            reverse("accounts:login"),
            {
                "identifier": "customer@test.com",
                "password": "StrongPass123!",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/accounts/login/?csrf=expired", response["Location"])
