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