from django.contrib.auth import get_user_model
from django.db import connection
from django.core.management.base import BaseCommand


PASSWORD = "Fahmy123$"
TARGETS = [
    "admin@test.com",
    "customer1@test.com",
    "customer2@test.com",
    "customer3@test.com",
    "producer1@test.com",
    "producer2@test.com",
    "producer3@test.com",
]


class Command(BaseCommand):
    help = "Reset passwords for seeded users and verify the hash update."

    def handle(self, *args, **options):
        User = get_user_model()
        username_field = User.USERNAME_FIELD
        db_settings = connection.settings_dict

        self.stdout.write(f"USERNAME FIELD: {username_field}")
        self.stdout.write(
            "DATABASE SETTINGS: "
            f"engine={db_settings.get('ENGINE', '')}, "
            f"name={db_settings.get('NAME', '')}, "
            f"user={db_settings.get('USER', '')}, "
            f"host={db_settings.get('HOST', '')}, "
            f"port={db_settings.get('PORT', '')}"
        )
        self.stdout.write(f"TOTAL USERS: {User.objects.count()}")
        self.stdout.write("ALL USERS:")
        for user in User.objects.all().order_by("id"):
            self.stdout.write(
                f"{user.id} {getattr(user, 'username', '')} {getattr(user, 'email', '')}"
            )

        for value in TARGETS:
            user = None

            if hasattr(User, "email"):
                user = User.objects.filter(email=value).first()

            if user is None:
                user = User.objects.filter(**{username_field: value}).first()

            if user is None:
                self.stdout.write(self.style.WARNING(f"NOT FOUND -> {value}"))
                continue

            user.set_password(PASSWORD)
            user.is_active = True
            user.save()
            password_ok = user.check_password(PASSWORD)

            self.stdout.write(
                self.style.SUCCESS(
                    "UPDATED -> "
                    f"id={user.id}, "
                    f"email={getattr(user, 'email', '')}, "
                    f"username={getattr(user, 'username', '')}, "
                    f"password_set_correctly={password_ok}"
                )
            )

        self.stdout.write("DONE")
