from decimal import Decimal
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from market_products.models import Product
from market_orders.models import Order, ProducerSubOrder, OrderItem, SubOrderStatusEvent

COMMISSION_RATE = Decimal("0.05")
DEFAULT_PASSWORD = "Fahmy123$"


class Command(BaseCommand):
    help = "Seed marketplace products, orders, and producer suborders for testing."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()

        # ----------------------------
        # Users (match existing seed_dev)
        # ----------------------------
        def upsert_user(username: str, email: str, role_attr_name: str | None, staff=False, superuser=False):
            user, _ = User.objects.get_or_create(username=username, defaults={"email": email})
            user.email = email
            user.set_password(DEFAULT_PASSWORD)
            user.is_staff = staff
            user.is_superuser = superuser

            if hasattr(User, "Role") and role_attr_name:
                # e.g. User.Role.PRODUCER
                role_value = getattr(User.Role, role_attr_name, None)
                if role_value is not None:
                    user.role = role_value

            user.save()
            return user

        admin = upsert_user("admin", "admin@test.com", "ADMIN", staff=True, superuser=True)
        producer1 = upsert_user("producer1", "producer1@test.com", "PRODUCER")
        producer2 = upsert_user("producer2", "producer2@test.com", "PRODUCER")
        customer1 = upsert_user("customer1", "customer1@test.com", "CUSTOMER")

        self.stdout.write(self.style.SUCCESS(f"Users ready: admin, producer1, producer2, customer1 (password={DEFAULT_PASSWORD})"))

        # ----------------------------
        # Products
        # ----------------------------
        p1_products = [
            ("P1 Apples Box", Decimal("40.00"), 50),
            ("P1 Olive Oil 1L", Decimal("95.00"), 30),
            ("P1 Carrots Bag", Decimal("15.00"), 80),
        ]
        p2_products = [
            ("P2 Chicken Pack", Decimal("110.00"), 40),
            ("P2 Milk 1L", Decimal("18.00"), 120),
            ("P2 Cheese 500g", Decimal("65.00"), 60),
        ]

        def get_or_create_product(producer, name, price, stock):
            obj, _ = Product.objects.get_or_create(
                producer=producer,
                name=name,
                defaults={
                    "price": price,
                    "stock_quantity": stock,
                    "low_stock_threshold": 5,
                    "is_active": True,
                },
            )
            return obj

        for name, price, stock in p1_products:
            get_or_create_product(producer1, name, price, stock)
        for name, price, stock in p2_products:
            get_or_create_product(producer2, name, price, stock)

        self.stdout.write(self.style.SUCCESS("Products ready (producer1 + producer2)"))

        # ----------------------------
        # Orders + ProducerSubOrders + Items
        # ----------------------------
        now = timezone.now()
        delivery_dates = [
            now + timedelta(hours=52),
            now + timedelta(hours=80),
            now + timedelta(hours=110),
        ]

        def recalc_suborder(sub: ProducerSubOrder) -> tuple[Decimal, Decimal]:
            subtotal = sub.items.aggregate(total=Sum("line_total"))["total"] or Decimal("0.00")
            commission = (subtotal * COMMISSION_RATE).quantize(Decimal("0.01"))
            payout = (subtotal - commission).quantize(Decimal("0.01"))

            sub.subtotal = subtotal
            sub.commission_amount = commission
            sub.producer_payout_amount = payout
            sub.save(update_fields=["subtotal", "commission_amount", "producer_payout_amount", "updated_at"])
            return subtotal, commission

        def create_order(i: int, delivery_dt):
            order = Order.objects.create(
                customer=customer1,
                status=Order.Status.CONFIRMED,
                delivery_address=f"Customer Address {i}, Bristol",
                customer_phone="07123456789",
                special_instructions="Leave at reception.",
            )

            sub1 = ProducerSubOrder.objects.create(
                order=order,
                producer=producer1,
                status=ProducerSubOrder.Status.PENDING,
                delivery_date=delivery_dt,
            )
            sub2 = ProducerSubOrder.objects.create(
                order=order,
                producer=producer2,
                status=ProducerSubOrder.Status.PENDING,
                delivery_date=delivery_dt,
            )

            # Items for producer1
            p1a = Product.objects.get(producer=producer1, name="P1 Apples Box")
            p1b = Product.objects.get(producer=producer1, name="P1 Carrots Bag")
            OrderItem.objects.create(
                suborder=sub1,
                product=p1a,
                product_name=p1a.name,
                unit_price=p1a.price,
                quantity=2,
            )
            OrderItem.objects.create(
                suborder=sub1,
                product=p1b,
                product_name=p1b.name,
                unit_price=p1b.price,
                quantity=4,
            )

            # Items for producer2
            p2a = Product.objects.get(producer=producer2, name="P2 Milk 1L")
            p2b = Product.objects.get(producer=producer2, name="P2 Cheese 500g")
            OrderItem.objects.create(
                suborder=sub2,
                product=p2a,
                product_name=p2a.name,
                unit_price=p2a.price,
                quantity=6,
            )
            OrderItem.objects.create(
                suborder=sub2,
                product=p2b,
                product_name=p2b.name,
                unit_price=p2b.price,
                quantity=1,
            )

            sub1_subtotal, sub1_commission = recalc_suborder(sub1)
            sub2_subtotal, sub2_commission = recalc_suborder(sub2)

            order.total_amount = (sub1_subtotal + sub2_subtotal).quantize(Decimal("0.01"))
            order.commission_total = (sub1_commission + sub2_commission).quantize(Decimal("0.01"))
            order.save(update_fields=["total_amount", "commission_total", "updated_at"])

            # Add a status event (and move one suborder forward) to make it look realistic
            SubOrderStatusEvent.objects.create(
                suborder=sub1,
                old_status=ProducerSubOrder.Status.PENDING,
                new_status=ProducerSubOrder.Status.CONFIRMED,
                note="Auto-confirmed by the marketplace",
                changed_by=admin,
            )
            sub1.status = ProducerSubOrder.Status.CONFIRMED
            sub1.save(update_fields=["status", "updated_at"])

        existing_marketplace_orders = Order.objects.filter(customer=customer1).count()
        if existing_marketplace_orders == 0:
            for idx, dt in enumerate(delivery_dates, start=1):
                create_order(idx, dt)
            self.stdout.write(self.style.SUCCESS("Created 3 marketplace orders (each has suborders for producer1 & producer2)"))
        else:
            self.stdout.write(self.style.WARNING(
                f"customer1 already has {existing_marketplace_orders} orders. Skipping order creation."
            ))

        self.stdout.write(self.style.SUCCESS(f"Marketplace seed complete. Login as producer1/producer2 (password={DEFAULT_PASSWORD})."))
