from market_accounts.models import User


def is_admin(user) -> bool:
    return (
        user.is_authenticated and
        (
            user.is_staff
            or user.is_superuser
            or getattr(user, "role", None) == User.Role.ADMIN
        )
    )


def is_producer(user) -> bool:
    return user.is_authenticated and getattr(user, "role", None) == User.Role.PRODUCER


def is_customer(user) -> bool:
    return user.is_authenticated and getattr(user, "role", None) == User.Role.CUSTOMER


def can_manage_producer_orders(user) -> bool:
    return is_admin(user) or is_producer(user)


def can_use_customer_checkout(user) -> bool:
    return is_admin(user) or is_customer(user)
