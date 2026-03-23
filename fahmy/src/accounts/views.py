from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.utils.http import url_has_allowed_host_and_scheme

from market_accounts.models import User


@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.user.is_authenticated:
        return _safe_redirect_or_default(request, next_url, request.user)

    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        password = request.POST.get("password", "")

        user = authenticate(request, username=identifier, password=password)

        # Try email -> username mapping
        if user is None and "@" in identifier:
            from django.contrib.auth import get_user_model
            U = get_user_model()
            try:
                u = U.objects.get(email__iexact=identifier)
                user = authenticate(request, username=u.username, password=password)
            except U.DoesNotExist:
                user = None

        if user is not None:
            login(request, user)
            return _safe_redirect_or_default(request, next_url, user)

        messages.error(request, "Invalid credentials. Please try again.")

    return render(request, "accounts/login.html", {"next": next_url})


def logout_view(request):
    logout(request)
    return redirect("/accounts/login/")


def _safe_redirect_or_default(request, next_url, user):
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    # Admin users land on the finance dashboard.
    if user.is_superuser or user.is_staff:
        return redirect("/finance/admin/dashboard/")

    if getattr(user, "role", None) == User.Role.PRODUCER:
        return redirect("/orders/producer/dashboard/")
    if getattr(user, "role", None) == User.Role.CUSTOMER:
        return redirect("/")
    if getattr(user, "role", None) == User.Role.ADMIN:
        return redirect("/finance/admin/dashboard/")

    return redirect("/")


def register_producer(request):
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()

    if request.method == "POST":
        business_name = request.POST.get("business_name", "").strip()
        contact_name = request.POST.get("contact_name", "").strip()
        email = request.POST.get("email", "").strip().lower()
        phone = request.POST.get("phone", "").strip()
        address = request.POST.get("address", "").strip()
        postcode = request.POST.get("postcode", "").strip()
        password = request.POST.get("password", "")
        confirm = request.POST.get("confirm_password", "")

        if not all([business_name, contact_name, email, phone, address, postcode, password, confirm]):
            messages.error(request, "Please fill in all fields.")
            return render(request, "accounts/register_producer.html")

        if password != confirm:
            messages.error(request, "Passwords do not match.")
            return render(request, "accounts/register_producer.html")

        if UserModel.objects.filter(email=email).exists():
            messages.error(request, "An account with this email already exists.")
            return render(request, "accounts/register_producer.html")

        user = UserModel.objects.create_user(
            username=email,
            email=email,
            password=password,
        )

        # Set role (uppercase)
        if hasattr(UserModel, "Role"):
            user.role = UserModel.Role.PRODUCER
        else:
            user.role = "PRODUCER"

        # Save extra info if fields exist
        user.first_name = contact_name
        for field, value in [
            ("business_name", business_name),
            ("phone", phone),
            ("address", address),
            ("postcode", postcode),
        ]:
            if hasattr(user, field):
                setattr(user, field, value)

        user.save()

        messages.success(request, "Producer account created. You can now log in.")
        return redirect("/accounts/login/")

    return render(request, "accounts/register_producer.html")
