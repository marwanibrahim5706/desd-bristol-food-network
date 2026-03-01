from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = "Seed development users (admin, producer, customer)"

    def handle(self, *args, **options):
        User = get_user_model()

        admin, _ = User.objects.get_or_create(username="admin", defaults={"email": "admin@test.com"})
        admin.set_password("123")
        admin.is_staff = True
        admin.is_superuser = True
        if hasattr(User, "Role"):
            admin.role = User.Role.ADMIN
        admin.save()

        producer, _ = User.objects.get_or_create(username="producer1", defaults={"email": "producer1@test.com"})
        producer.set_password("123")
        producer.is_staff = False
        producer.is_superuser = False
        if hasattr(User, "Role"):
            producer.role = User.Role.PRODUCER
        producer.save()

        customer, _ = User.objects.get_or_create(username="customer1", defaults={"email": "customer1@test.com"})
        customer.set_password("123")
        customer.is_staff = False
        customer.is_superuser = False
        if hasattr(User, "Role"):
            customer.role = User.Role.CUSTOMER
        customer.save()

        self.stdout.write(self.style.SUCCESS("✅ Seeded admin/producer/customer (password=123)"))