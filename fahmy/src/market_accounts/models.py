from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        PRODUCER = "PRODUCER", "Producer"
        ADMIN = "ADMIN", "Admin"

    role = models.CharField(
        max_length=20,
        choices=Role.choices,
        default=Role.CUSTOMER
    )

    # TC-001
    business_name = models.CharField(max_length=255, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    postcode = models.CharField(max_length=20, blank=True, null=True)

    @property
    def display_name(self):
        business_name = (self.business_name or "").strip()
        if business_name:
            return business_name

        full_name = self.get_full_name().strip()
        if full_name:
            return full_name

        first_name = (self.first_name or "").strip()
        if first_name:
            return first_name

        return self.username
