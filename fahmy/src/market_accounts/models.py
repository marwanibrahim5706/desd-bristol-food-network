from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    class Role(models.TextChoices):
        CUSTOMER = "CUSTOMER", "Customer"
        PRODUCER = "PRODUCER", "Producer"
        ADMIN = "ADMIN", "Admin"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)