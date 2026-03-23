from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods, require_POST
from django.utils.http import url_has_allowed_host_and_scheme

from market_accounts.models import User
from .forms import CustomerRegistrationForm, ProducerRegistrationForm


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


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("/accounts/login/")


def _safe_redirect_or_default(request, next_url, user):
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    # Django admin flags first
    if user.is_superuser or user.is_staff:
        return redirect("/admin/")

    if getattr(user, "role", None) == User.Role.PRODUCER:
        return redirect("/orders/producer/dashboard/")
    if getattr(user, "role", None) == User.Role.CUSTOMER:
        return redirect("/discover/")
    if getattr(user, "role", None) == User.Role.ADMIN:
        return redirect("/admin/")

    return redirect("/discover/")


def register_producer(request):
    if request.user.is_authenticated:
        return _safe_redirect_or_default(request, "", request.user)

    if request.method == "POST":
        form = ProducerRegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Producer account created. You can now log in.")
            return redirect("/accounts/login/")
        messages.error(request, "Please correct the highlighted errors.")
    else:
        form = ProducerRegistrationForm()

    return render(request, "accounts/register_producer.html", {"form": form})


@require_http_methods(["GET", "POST"])
def register_customer(request):
    if request.user.is_authenticated:
        return _safe_redirect_or_default(request, "", request.user)

    if request.method == "POST":
        form = CustomerRegistrationForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Customer account created. You can now log in.")
            return redirect("/accounts/login/")
        messages.error(request, "Please correct the highlighted errors.")
    else:
        form = CustomerRegistrationForm()

    return render(request, "accounts/register_customer.html", {"form": form})
