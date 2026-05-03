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
from market_finance.services import generate_weekly_settlements


COMMISSION_RATE = Decimal("0.05")
DEFAULT_PASSWORD = "Fahmy123$"
SEEDED_ORDER_PREFIX = "Customer Address"
TC025_ORDER_PREFIX = "TC-025 Finance Seed"
SEED_REFERENCE = "market_seed"


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
    help = "Seed all existing tables with safe marketplace data for testing without altering schema."

    def handle(self, *args, **options):
        random.seed(42)
        now = timezone.now()
        User = get_user_model()

        self.stdout.write(self.style.WARNING("Starting marketplace seed..."))

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
                    "address": "Flat 1, Market Street, Bristol",
                    "postcode": "BS4 4DD",
                },
            ),
            upsert_user(
                "customer2", "customer2@test.com", "CUSTOMER",
                extra={
                    "phone": "07222222222",
                    "address": "Flat 2, Market Street, Bristol",
                    "postcode": "BS5 5EE",
                },
            ),
            upsert_user(
                "customer3", "customer3@test.com", "CUSTOMER",
                extra={
                    "phone": "07333333333",
                    "address": "Flat 3, Market Street, Bristol",
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
                ("P1 Apples Box", Decimal("40.00"), 50, "https://images.unsplash.com/photo-1567306226416-28f0efdc88ce?auto=format&fit=crop&w=1200&q=80", "fruit_veg", True, Decimal("8.5"), "autumn", "", "Crisp seasonal apples grown close to Bristol, ideal for snacking, baking, and lunch boxes."),
                ("P1 Carrots Bag", Decimal("15.00"), 35, "https://commons.wikimedia.org/wiki/Special:Redirect/file/Carrots.JPG", "fruit_veg", True, Decimal("6.2"), "autumn", "", "Sweet local carrots with a firm crunch, perfect for roasting, soups, and fresh salads."),
                ("P1 Potatoes Sack", Decimal("22.00"), 25, "https://images.unsplash.com/photo-1518977676601-b53f82aba655?auto=format&fit=crop&w=1200&q=80", "fruit_veg", False, Decimal("7.0"), "all_year", "", "A hearty sack of all-round potatoes for mashing, baking, roasting, and weekly kitchen prep."),
                ("P1 Olive Oil 1L", Decimal("95.00"), 12, "https://images.unsplash.com/photo-1474979266404-7eaacbcd87c5?auto=format&fit=crop&w=1200&q=80", "other", False, Decimal("42.0"), "all_year", "", "Smooth extra virgin olive oil for dressings, roasting vegetables, and finishing warm dishes."),
                ("P1 Tomatoes Crate", Decimal("32.00"), 4, "https://images.unsplash.com/photo-1546094096-0df4bcaaa337?auto=format&fit=crop&w=1200&q=80", "fruit_veg", True, Decimal("5.5"), "summer", "", "Juicy summer tomatoes with bright flavour for salads, sauces, sandwiches, and roasting."),   # low stock test
                ("P1 Eggs Tray", Decimal("27.00"), 3, "https://images.unsplash.com/photo-1506976785307-8732e854ad03?auto=format&fit=crop&w=1200&q=80", "eggs", True, Decimal("9.0"), "all_year", "Eggs", "Free range eggs with bright yolks, useful for breakfasts, baking, and protein-rich meals."),        # low stock test
            ],
            producers[1]: [
                ("P2 Milk 1L", Decimal("18.00"), 120, "https://images.unsplash.com/photo-1550583724-b2692b85b150?auto=format&fit=crop&w=1200&q=80", "dairy", False, Decimal("12.0"), "all_year", "Milk", "Fresh whole milk from a local dairy, ready for breakfast, baking, tea, and coffee."),
                ("P2 Cheese 500g", Decimal("65.00"), 60, "https://images.unsplash.com/photo-1452195100486-9cc805987862?auto=format&fit=crop&w=1200&q=80", "dairy", False, Decimal("12.0"), "all_year", "Milk", "Rich farmhouse-style cheese with a creamy texture, good for boards, toasties, and cooking."),
                ("P2 Yogurt Pack", Decimal("24.00"), 18, "https://images.unsplash.com/photo-1488477181946-6428a0291777?auto=format&fit=crop&w=1200&q=80", "dairy", False, Decimal("12.0"), "all_year", "Milk", "Creamy yogurt pots for breakfasts, desserts, smoothies, or simple fruit pairings."),
                ("P2 Butter Block", Decimal("19.00"), 7, "https://images.unsplash.com/photo-1589985270826-4b7bb135bc9d?auto=format&fit=crop&w=1200&q=80", "dairy", False, Decimal("12.0"), "all_year", "Milk", "Golden butter block for baking, spreading, pan cooking, and finishing vegetables."),
            ],
            producers[2]: [
                ("P3 Fresh Bread", Decimal("12.50"), 80, "https://images.unsplash.com/photo-1509440159596-0249088772ff?auto=format&fit=crop&w=1200&q=80", "bakery", False, Decimal("3.5"), "all_year", "Gluten", "Freshly baked crusty bread for sandwiches, soup lunches, toast, and sharing."),
                ("P3 Croissant Box", Decimal("20.00"), 30, "https://commons.wikimedia.org/wiki/Special:Redirect/file/Croissants.jpg", "bakery", False, Decimal("3.5"), "all_year", "Gluten, Milk", "A box of buttery croissants with flaky layers, best for breakfast meetings and treats."),
                ("P3 Wholemeal Loaf", Decimal("9.50"), 22, "https://images.pexels.com/photos/30675188/pexels-photo-30675188.jpeg?cs=srgb&dl=pexels-christina99999-30675188.jpg&fm=jpg", "bakery", True, Decimal("3.5"), "all_year", "Gluten", "Nutty wholemeal loaf baked for everyday sandwiches, toast, and soup pairings."),
                ("P3 Donut Pack", Decimal("16.00"), 14, "https://images.unsplash.com/photo-1551024601-bec78aea704b?auto=format&fit=crop&w=1200&q=80", "bakery", False, Decimal("3.5"), "all_year", "Gluten, Milk, Eggs", "Soft sweet donuts for dessert tables, staff treats, and weekend sharing boxes."),
                ("P3 Brioche", Decimal("11.00"), 2, "https://images.pexels.com/photos/7884507/pexels-photo-7884507.jpeg?cs=srgb&dl=pexels-ilariam-7884507.jpg&fm=jpg", "bakery", False, Decimal("3.5"), "all_year", "Gluten, Milk, Eggs", "Soft enriched brioche with a tender crumb, great for brunch, toast, or puddings."),          # low stock test
            ],
        }

        products_by_name = {}

        for producer, rows in catalog.items():
            for name, price, stock, image_url, category, is_organic, food_miles, seasonal_availability, allergens, description in rows:
                product, _ = Product.objects.get_or_create(
                    producer=producer,
                    name=name,
                    defaults={
                        "price": price,
                        "image_url": image_url,
                        "category": category,
                        "is_organic": is_organic,
                        "food_miles": food_miles,
                        "seasonal_availability": seasonal_availability,
                        "allergens": allergens,
                        "description": description,
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
                if hasattr(product, "image_url"):
                    product.image_url = image_url
                if hasattr(product, "category"):
                    product.category = category
                if hasattr(product, "is_organic"):
                    product.is_organic = is_organic
                if hasattr(product, "food_miles"):
                    product.food_miles = food_miles
                if hasattr(product, "seasonal_availability"):
                    product.seasonal_availability = seasonal_availability
                if hasattr(product, "allergens"):
                    product.allergens = allergens
                if hasattr(product, "description"):
                    product.description = description
                if hasattr(product, "is_active"):
                    product.is_active = True
                product.save()
                products_by_name[name] = product

        legacy_products = {
            "P2 Chicken Pack": {
                "image_url": "https://images.unsplash.com/photo-1587593810167-a84920ea0781?auto=format&fit=crop&w=1200&q=80",
                "category": "meat",
                "allergens": "",
                "food_miles": Decimal("14.0"),
                "seasonal_availability": "all_year",
                "description": "Prepared local chicken portions for roasting, tray bakes, curries, and weekly meal prep.",
            },
        }
        product_fields = {field.name for field in Product._meta.fields}
        for name, values in legacy_products.items():
            update_values = {key: value for key, value in values.items() if key in product_fields}
            if update_values:
                Product.objects.filter(name=name).update(**update_values)
        Product.objects.filter(name="bread").update(is_active=False)

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
                delivery_address=f"{SEEDED_ORDER_PREFIX} {idx}, Bristol",
                customer_phone=getattr(customer, "phone", "07000000000") or "07000000000",
                special_instructions="Ring the bell and leave safely.",
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
                        note="Auto-confirmed by the marketplace",
                        changed_by=admin,
                    )
                    sub.status = ProducerSubOrder.Status.CONFIRMED
                    sub.save(update_fields=["status", "updated_at"])
                elif final_status == ProducerSubOrder.Status.READY:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CONFIRMED,
                        note="Confirmed by the producer",
                        changed_by=admin,
                    )
                    current = ProducerSubOrder.Status.CONFIRMED
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.READY,
                        note="Packed and ready",
                        changed_by=producer,
                    )
                    sub.status = ProducerSubOrder.Status.READY
                    sub.save(update_fields=["status", "updated_at"])
                elif final_status == ProducerSubOrder.Status.DELIVERED:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CONFIRMED,
                        note="Confirmed by the producer",
                        changed_by=admin,
                    )
                    current = ProducerSubOrder.Status.CONFIRMED
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.READY,
                        note="Packed and ready",
                        changed_by=producer,
                    )
                    current = ProducerSubOrder.Status.READY
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.DELIVERED,
                        note="Delivered successfully",
                        changed_by=admin,
                    )
                    sub.status = ProducerSubOrder.Status.DELIVERED
                    sub.save(update_fields=["status", "updated_at"])
                elif final_status == ProducerSubOrder.Status.CANCELLED:
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=current,
                        new_status=ProducerSubOrder.Status.CANCELLED,
                        note="Cancelled by the customer",
                        changed_by=admin,
                    )
                    sub.status = ProducerSubOrder.Status.CANCELLED
                    sub.save(update_fields=["status", "updated_at"])

            order.total_amount = total_amount.quantize(Decimal("0.01"))
            order.commission_total = commission_total.quantize(Decimal("0.01"))
            order.save(update_fields=["total_amount", "commission_total", "updated_at"])
            return order

        existing_seed_orders = Order.objects.filter(delivery_address__startswith=SEEDED_ORDER_PREFIX).count()
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
                            [("P1 Eggs Tray", 2), ("P2 Milk 1L", 4)],
                            ProducerSubOrder.Status.DELIVERED,
                        ),
                        (
                            producers[2],
                            [("P3 Croissant Box", 3), ("P3 Donut Pack", 2)],
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
                f"Seeded marketplace orders already exist ({existing_seed_orders}). Skipping order creation."
            ))

        # ------------------------------------------------------------------
        # 3b) TC-025 FINANCE REPORTING DATA
        # ------------------------------------------------------------------
        # The finance admin test case requires at least two weeks of completed
        # orders and explicit examples for £100 and £150 commission checks.
        existing_tc025_orders = Order.objects.filter(delivery_address__startswith=TC025_ORDER_PREFIX).count()
        if existing_tc025_orders == 0:
            def build_tc025_order(customer, delivery_dt, producer_lines, idx):
                order = Order.objects.create(
                    customer=customer,
                    status=Order.Status.COMPLETED,
                    delivery_address=f"{TC025_ORDER_PREFIX} {idx}, Bristol",
                    customer_phone=getattr(customer, "phone", "07000000000") or "07000000000",
                    special_instructions="Finance reporting seed order.",
                )
                total_amount = Decimal("0.00")
                commission_total = Decimal("0.00")

                for producer, amount, product_name in producer_lines:
                    product = Product.objects.filter(producer=producer, name=product_name).first()
                    if product is None:
                        product = Product.objects.filter(producer=producer).order_by("id").first()
                    sub = ProducerSubOrder.objects.create(
                        order=order,
                        producer=producer,
                        status=ProducerSubOrder.Status.DELIVERED,
                        delivery_date=delivery_dt,
                    )
                    OrderItem.objects.create(
                        suborder=sub,
                        product=product,
                        product_name=product.name if product else "Finance reporting item",
                        unit_price=amount,
                        quantity=1,
                    )
                    sub.subtotal = amount
                    sub.commission_amount = (amount * COMMISSION_RATE).quantize(Decimal("0.01"))
                    sub.producer_payout_amount = (amount - sub.commission_amount).quantize(Decimal("0.01"))
                    sub.save(update_fields=["subtotal", "commission_amount", "producer_payout_amount", "updated_at"])
                    SubOrderStatusEvent.objects.create(
                        suborder=sub,
                        old_status=ProducerSubOrder.Status.READY,
                        new_status=ProducerSubOrder.Status.DELIVERED,
                        note="Delivered successfully for finance reporting.",
                        changed_by=admin,
                    )
                    total_amount += sub.subtotal
                    commission_total += sub.commission_amount

                order.total_amount = total_amount.quantize(Decimal("0.01"))
                order.commission_total = commission_total.quantize(Decimal("0.01"))
                order.save(update_fields=["total_amount", "commission_total", "updated_at"])
                return order

            build_tc025_order(
                customers[0],
                now - timedelta(days=10),
                [(producers[0], Decimal("100.00"), "P1 Apples Box")],
                1,
            )
            build_tc025_order(
                customers[1],
                now - timedelta(days=3),
                [
                    (producers[0], Decimal("80.00"), "P1 Tomatoes Crate"),
                    (producers[1], Decimal("70.00"), "P2 Cheese 500g"),
                ],
                2,
            )
            build_tc025_order(
                customers[2],
                now - timedelta(days=17),
                [(producers[2], Decimal("248.50"), "P3 Croissant Box")],
                3,
            )
            generate_weekly_settlements(actor=admin)
            self.stdout.write(self.style.SUCCESS("TC-025 finance reporting orders and settlements seeded."))
        else:
            generate_weekly_settlements(actor=admin)
            self.stdout.write(self.style.WARNING(
                f"TC-025 finance reporting orders already exist ({existing_tc025_orders}). Refreshed settlements."
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

            # choose useful products for alerts
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
                    data["reference"] = SEED_REFERENCE

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
                    f"Product '{product_for_alert.name}' is below threshold.",
                    product=product_for_alert,
                )

            # producer new-order notifications
            for idx, p in enumerate(producers):
                producer_product = Product.objects.filter(producer=p).order_by("id").first() or fallback_product
                create_notification(
                    p,
                    "New incoming order",
                    "You have a new producer suborder.",
                    product=producer_product,
                )

            # customer order-update notifications
            for c in customers:
                customer_product = fallback_product
                create_notification(
                    c,
                    "Order update",
                    "Your order status has changed.",
                    product=customer_product,
                )

            self.stdout.write(self.style.SUCCESS("Notifications seeded where possible."))
        else:
            self.stdout.write("Notification model not found. Skipping alert seed.")


        # ------------------------------------------------------------------
        # 6) Final summary
        # ------------------------------------------------------------------
        self.stdout.write(self.style.SUCCESS("Marketplace seed completed successfully."))
        self.stdout.write(f"Login accounts use password: {DEFAULT_PASSWORD}")
        self.stdout.write("Suggested test logins: admin, producer1, producer2, producer3, customer1, customer2, customer3")
