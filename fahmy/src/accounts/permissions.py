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
    return (
        user.is_authenticated
        and getattr(user, "role", None) == User.Role.PRODUCER
        and not is_admin(user)
    )
