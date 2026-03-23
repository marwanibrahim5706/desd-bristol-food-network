from decimal import Decimal
from datetime import timedelta
import random

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from market_products.models import Product
from market_orders.models import Order, ProducerSubOrder, OrderItem, SubOrderStatusEvent


COMMISSION_RATE = Decimal("0.05")
DEFAULT_PASSWORD = "123"
SEED_TAG = "[SEED_FULL_DEMO]"


def model_field_names(model):
    return {f.name for f in model._meta.get_fields() if hasattr(f, "name")}


def set_if_hasattr(obj, **kwargs):
    """
    Set only attributes/fields that exist on the model instance.
    """
    names = model_field_names(obj.__class__)
    changed = False
    for key, value in kwargs.items():
        if key in names or hasattr(obj, key):
            setattr(obj, key, value)
            changed = True
    if changed:
        obj.save()
    return obj


def enum_value(enum_cls, name, fallback=None):
    if enum_cls and hasattr(enum_cls, name):
        return getattr(enum_cls, name)
    return fallback


class Command(BaseCommand):
    help = "Seed all existing tables with safe demo data for testing without altering schema."

    def handle(self, *args, **options):
        random.seed(42)
        now = timezone.now()
        User = get_user_model()

        self.stdout.write(self.style.WARNING("Starting full demo seed..."))

        # ------------------------------------------------------------------
        # 1) USERS
        # ------------------------------------------------------------------
        def upsert_user(username, email, role_attr_name=None, staff=False, superuser=False, extra=None):
            extra = extra or {}
            user, _ = User.objects.get_or_create(username=username, defaults={"email": email})
            user.email = email
            user.set_password(DEFAULT_PASSWORD)

            if hasattr(user, "is_staff"):
                user.is_staff = staff
            if hasattr(user, "is_superuser"):
                user.is_superuser = superuser

            if hasattr(User, "Role") and role_attr_name:
                role_value = getattr(User.Role, role_attr_name, None)
                if role_value is not None and hasattr(user, "role"):
                    user.role = role_value

            for k, v in extra.items():
                if hasattr(user, k):
                    setattr(user, k, v)

            user.save()
            return user

        admin = upsert_user(
            "admin", "admin@test.com", "ADMIN", staff=True, superuser=True
        )

        producers = [
            upsert_user(
                "producer1", "producer1@test.com", "PRODUCER",
                extra={
                    "business_name": "Green Farm Co",
                    "phone": "07000000001",
                    "address": "Farm Lane 1, Bristol",
                    "postcode": "BS1 1AA",
                },
            ),
            upsert_user(
                "producer2", "producer2@test.com", "PRODUCER",
                extra={
                    "business_name": "Fresh Dairy House",
                    "phone": "07000000002",
                    "address": "Market Road 12, Bristol",
                    "postcode": "BS2 2BB",
                },
            ),
            upsert_user(
                "producer3", "producer3@test.com", "PRODUCER",
                extra={
                    "business_name": "Bakers Corner",
                    "phone": "07000000003",
                    "address": "Bread Street 8, Bristol",
                    "postcode": "BS3 3CC",
                },
            ),
        ]

        customers = [
            upsert_user(
                "customer1", "customer1@test.com", "CUSTOMER",
                extra={
                    "phone": "07111111111",
                    "address": "Flat 1, Demo Address, Bristol",
                    "postcode": "BS4 4DD",
                },
            ),
            upsert_user(
                "customer2", "customer2@test.com", "CUSTOMER",
                extra={
                    "phone": "07222222222",
                    "address": "Flat 2, Demo Address, Bristol",
                    "postcode": "BS5 5EE",
                },
            ),
            upsert_user(
                "customer3", "customer3@test.com", "CUSTOMER",
                extra={
                    "phone": "07333333333",
                    "address": "Flat 3, Demo Address, Bristol",
                    "postcode": "BS6 6FF",
                },
            ),
        ]

        self.stdout.write(self.style.SUCCESS("Users seeded/updated."))

        # ------------------------------------------------------------------
        # 2) PRODUCTS
        # ------------------------------------------------------------------
        catalog = {
            producers[0]: [
                ("P1 Apples Box", Decimal("40.00"), 50),
                ("P1 Carrots Bag", Decimal("15.00"), 35),
                ("P1 Potatoes Sack", Decimal("22.00"), 25),
                ("P1 Olive Oil 1L", Decimal("95.00"), 12),
                ("P1 Tomatoes Crate", Decimal("32.00"), 4),   # low stock test
            ],
            producers[1]: [
                ("P2 Milk 1L", Decimal("18.00"), 120),
                ("P2 Cheese 500g", Decimal("65.00"), 60),
                ("P2 Yogurt Pack", Decimal("24.00"), 18),
                ("P2 Butter Block", Decimal("19.00"), 7),
                ("P2 Eggs Tray", Decimal("27.00"), 3),        # low stock test
            ],
            producers[2]: [
                ("P3 Fresh Bread", Decimal("12.50"), 80),
                ("P3 Croissant Box", Decimal("20.00"), 30),
                ("P3 Wholemeal Loaf", Decimal("9.50"), 22),
                ("P3 Donut Pack", Decimal("16.00"), 14),
                ("P3 Brioche", Decimal("11.00"), 2),          # low stock test
            ],
        }

        products_by_name = {}

        for producer, rows in catalog.items():
            for name, price, stock in rows:
                product, _ = Product.objects.get_or_create(
                    producer=producer,
                    name=name,
                    defaults={
                        "price": price,
                        "stock_quantity": stock,
                        "low_stock_threshold": 5,
                        "is_active": True,
                    },
                )
                # keep core values fresh
                product.price = price
                if hasattr(product, "stock_quantity"):
                    product.stock_quantity = stock
                if hasattr(product, "low_stock_threshold"):
                    product.low_stock_threshold = 5
                if hasattr(product, "is_active"):
                    product.is_active = True
                product.save()
                products_by_name[name] = product

        self.stdout.write(self.style.SUCCESS("Products seeded/updated."))

        # ------------------------------------------------------------------
        # 3) ORDERS / SUBORDERS / ITEMS / STATUS EVENTS
        # ------------------------------------------------------------------
        def recalc_suborder(sub):
            subtotal = sub.items.aggregate(total=Sum("line_total"))["total"] or Decimal("0.00")
            commission = (subtotal * COMMISSION_RATE).quantize(Decimal("0.01"))
            payout = (subtotal - commission).quantize(Decimal("0.01"))

            sub.subtotal = subtotal
            sub.commission_amount = commission
            sub.producer_payout_amount = payout
            sub.save(update_fields=["subtotal", "commission_amount", "producer_payout_amount", "updated_at"])
            return subtotal, commission

        def build_order(customer, status, delivery_dt, producer_item_specs, idx):
            """
            producer_item_specs = [
              (producer_obj, [(product_name, qty), ...], final_sub_status),
              ...
            ]
            """
            order = Order.objects.create(
                customer=customer,
                status=status,
                delivery_address=f"{SEED_TAG} Demo Address {idx}, Bristol",
                customer_phone=getattr(customer, "phone", "07000000000") or "07000000000",
                special_instructions=f"{SEED_TAG} Ring the bell and leave safely.",
            )

            total_amount = Decimal("0.00")
            commission_total = Decimal("0.00")

            for producer, item_specs, final_status in producer_item_specs:
                sub = ProducerSubOrder.objects.create(
                    order=order,
                    producer=producer,
                    status=ProducerSubOrder.Status.PENDING,
                    delivery_date=delivery_dt,
                )

                for product_name, qty in item_specs:
                    product = products_by_name[product_name]
                    OrderItem.objects.create(
                        suborder=sub,
                        product=product,
                        product_name=product.name,
                        unit_price=product.price,
                        quantity=qty,
                    )

                sub_subtotal, sub_commission = recalc_suborder(sub)
                total_amount += sub_subtotal
                commission_total += sub_commission

                # build history to reach final status
                current = ProducerSubOrder.Status.PENDING
                if final_status == ProducerSubOrder.Status.PENDING:
                    pass
                elif final_status == ProducerSubOrder.Status.CONFIRMED:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CONFIRMED,
                        note=f"{SEED_TAG} Auto-confirmed for demo",
                        changed_by=admin,
                    )
                    sub.status = ProducerSubOrder.Status.CONFIRMED
                    sub.save(update_fields=["status", "updated_at"])
                elif final_status == ProducerSubOrder.Status.READY:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CONFIRMED,
                        note=f"{SEED_TAG} Confirmed for demo",
                        changed_by=admin,
                    )
                    current = ProducerSubOrder.Status.CONFIRMED
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.READY,
                        note=f"{SEED_TAG} Packed and ready",
                        changed_by=producer,
                    )
                    sub.status = ProducerSubOrder.Status.READY
                    sub.save(update_fields=["status", "updated_at"])
                elif final_status == ProducerSubOrder.Status.DELIVERED:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CONFIRMED,
                        note=f"{SEED_TAG} Confirmed for demo",
                        changed_by=admin,
                    )
                    current = ProducerSubOrder.Status.CONFIRMED
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.READY,
                        note=f"{SEED_TAG} Packed and ready",
                        changed_by=producer,
                    )
                    current = ProducerSubOrder.Status.READY
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.DELIVERED,
                        note=f"{SEED_TAG} Delivered successfully",
                        changed_by=admin,
                    )
                    sub.status = ProducerSubOrder.Status.DELIVERED
                    sub.save(update_fields=["status", "updated_at"])
                elif final_status == ProducerSubOrder.Status.CANCELLED:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CANCELLED,
                        note=f"{SEED_TAG} Cancelled for demo",
                        changed_by=admin,
                    )
                    sub.status = ProducerSubOrder.Status.CANCELLED
                    sub.save(update_fields=["status", "updated_at"])

            order.total_amount = total_amount.quantize(Decimal("0.01"))
            order.commission_total = commission_total.quantize(Decimal("0.01"))
            order.save(update_fields=["total_amount", "commission_total", "updated_at"])
            return order

        existing_seed_orders = Order.objects.filter(delivery_address__icontains=SEED_TAG).count()
        if existing_seed_orders == 0:
            order_specs = [
                # Single-producer future
                (
                    customers[0],
                    Order.Status.CONFIRMED,
                    now + timedelta(hours=52),
                    [
                        (
                            producers[0],
                            [("P1 Apples Box", 2), ("P1 Carrots Bag", 3)],
                            ProducerSubOrder.Status.PENDING,
                        )
                    ],
                ),
                # Multi-producer future
                (
                    customers[0],
                    Order.Status.CONFIRMED,
                    now + timedelta(hours=72),
                    [
                        (
                            producers[0],
                            [("P1 Olive Oil 1L", 1), ("P1 Tomatoes Crate", 2)],
                            ProducerSubOrder.Status.CONFIRMED,
                        ),
                        (
                            producers[1],
                            [("P2 Milk 1L", 6), ("P2 Cheese 500g", 1)],
                            ProducerSubOrder.Status.PENDING,
                        ),
                    ],
                ),
                # Multi-producer future ready
                (
                    customers[1],
                    Order.Status.CONFIRMED,
                    now + timedelta(hours=96),
                    [
                        (
                            producers[1],
                            [("P2 Yogurt Pack", 2), ("P2 Butter Block", 3)],
                            ProducerSubOrder.Status.READY,
                        ),
                        (
                            producers[2],
                            [("P3 Fresh Bread", 4), ("P3 Croissant Box", 2)],
                            ProducerSubOrder.Status.CONFIRMED,
                        ),
                    ],
                ),
                # Delivered past order
                (
                    customers[1],
                    Order.Status.COMPLETED,
                    now - timedelta(days=3),
                    [
                        (
                            producers[0],
                            [("P1 Potatoes Sack", 1), ("P1 Apples Box", 1)],
                            ProducerSubOrder.Status.DELIVERED,
                        )
                    ],
                ),
                # Delivered past multi-producer
                (
                    customers[2],
                    Order.Status.COMPLETED,
                    now - timedelta(days=5),
                    [
                        (
                            producers[1],
                            [("P2 Eggs Tray", 2), ("P2 Milk 1L", 4)],
                            ProducerSubOrder.Status.DELIVERED,
                        ),
                        (
                            producers[2],
                            [("P3 Wholemeal Loaf", 3), ("P3 Donut Pack", 2)],
                            ProducerSubOrder.Status.DELIVERED,
                        ),
                    ],
                ),
                # Cancelled order
                (
                    customers[2],
                    Order.Status.CANCELLED,
                    now + timedelta(hours=60),
                    [
                        (
                            producers[2],
                            [("P3 Brioche", 2)],
                            ProducerSubOrder.Status.CANCELLED,
                        )
                    ],
                ),
            ]

            for idx, (customer, order_status, delivery_dt, spec) in enumerate(order_specs, start=1):
                build_order(customer, order_status, delivery_dt, spec, idx)

            self.stdout.write(self.style.SUCCESS("Orders, suborders, items, and status history seeded."))
        else:
            self.stdout.write(self.style.WARNING(
                f"Seed-tagged demo orders already exist ({existing_seed_orders}). Skipping order creation."
            ))
        # ------------------------------------------------------------------
        # 4) ALERT NOTIFICATIONS (if model exists)
        # ------------------------------------------------------------------
        try:
            Notification = apps.get_model("market_alerts", "Notification")
        except LookupError:
            Notification = None

        if Notification:
            notif_fields = model_field_names(Notification)

            # choose useful demo products for alerts
            low_stock_products = list(
                Product.objects.filter(stock_quantity__lte=5).order_by("id")
            )
            fallback_product = Product.objects.order_by("id").first()

            def create_notification(user, title, message, product=None):
                product = product or fallback_product
                data = {}

                if "user" in notif_fields:
                    data["user"] = user
                elif "recipient" in notif_fields:
                    data["recipient"] = user

                if "title" in notif_fields:
                    data["title"] = title

                if "message" in notif_fields:
                    data["message"] = message
                elif "content" in notif_fields:
                    data["content"] = message
                elif "body" in notif_fields:
                    data["body"] = message

                if "is_read" in notif_fields:
                    data["is_read"] = False
                if "read" in notif_fields:
                    data["read"] = False

                if "notification_type" in notif_fields:
                    data["notification_type"] = "SYSTEM"
                elif "type" in notif_fields:
                    data["type"] = "SYSTEM"

                # IMPORTANT: your Notification model requires product_id
                if "product" in notif_fields and product is not None:
                    data["product"] = product

                if "reference" in notif_fields:
                    data["reference"] = SEED_TAG

                # isolate each insert so one failure does not break the whole seed
                try:
                    with transaction.atomic():
                        Notification.objects.create(**data)
                except Exception as e:
                    self.stdout.write(
                        self.style.WARNING(f"Skipping notification seed for {user.username}: {e}")
                    )

            # producer low-stock notifications
            for idx, p in enumerate(producers):
                product_for_alert = low_stock_products[idx % len(low_stock_products)] if low_stock_products else fallback_product
                create_notification(
                    p,
                    "Low stock alert",
                    f"{SEED_TAG} Product '{product_for_alert.name}' is below threshold.",
                    product=product_for_alert,
                )

            # producer new-order notifications
            for idx, p in enumerate(producers):
                producer_product = Product.objects.filter(producer=p).order_by("id").first() or fallback_product
                create_notification(
                    p,
                    "New incoming order",
                    f"{SEED_TAG} You have a new producer suborder.",
                    product=producer_product,
                )

            # customer order-update notifications
            for c in customers:
                customer_product = fallback_product
                create_notification(
                    c,
                    "Order update",
                    f"{SEED_TAG} Your order status has changed.",
                    product=customer_product,
                )

            self.stdout.write(self.style.SUCCESS("Notifications seeded where possible."))
        else:
            self.stdout.write("Notification model not found. Skipping alert seed.")


        # ------------------------------------------------------------------
        # 6) Final summary
        # ------------------------------------------------------------------
        self.stdout.write(self.style.SUCCESS("Full demo seed completed successfully."))
        self.stdout.write(f"Login accounts use password: {DEFAULT_PASSWORD}")
        self.stdout.write("Suggested test logins: admin, producer1, producer2, producer3, customer1, customer2, customer3")