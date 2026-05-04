from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.cache import cache
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST
import logging

from market_accounts.models import User

from .forms import CustomerRegistrationForm, ProducerRegistrationForm

logger = logging.getLogger(__name__)
LOGIN_ATTEMPT_LIMIT = 5
LOGIN_LOCKOUT_SECONDS = 300


def _login_attempt_cache_key(request, identifier):
    identifier = (identifier or "").strip().lower() or "blank"
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip_address = (forwarded_for.split(",")[0] or request.META.get("REMOTE_ADDR") or "unknown").strip()
    return f"login-attempts:{identifier}:{ip_address}"


@require_http_methods(["GET", "POST"])
def login_view(request):
    next_url = request.GET.get("next") or request.POST.get("next") or ""

    if request.user.is_authenticated:
        return _safe_redirect_or_default(request, next_url, request.user)

    if request.GET.get("csrf") == "expired":
        messages.error(request, "Your sign-in page expired. Please try signing in again.")

    if request.method == "POST":
        identifier = request.POST.get("identifier", "").strip()
        password = request.POST.get("password", "")
        attempt_key = _login_attempt_cache_key(request, identifier)
        failed_attempts = cache.get(attempt_key, 0)

        if failed_attempts >= LOGIN_ATTEMPT_LIMIT:
            logger.warning("Login temporarily locked for identifier %s", identifier or "<blank>")
            messages.error(request, "Too many failed sign-in attempts. Please wait a few minutes and try again.")
            return render(request, "accounts/login.html", {"next": next_url}, status=429)

        user = authenticate(request, username=identifier, password=password)

        if user is None and "@" in identifier:
            from django.contrib.auth import get_user_model

            user_model = get_user_model()
            try:
                matched_user = user_model.objects.get(email__iexact=identifier)
                user = authenticate(request, username=matched_user.username, password=password)
            except user_model.DoesNotExist:
                user = None

        if user is not None:
            cache.delete(attempt_key)
            login(request, user)
            if request.POST.get("remember_me"):
                request.session.set_expiry(1209600)
            else:
                request.session.set_expiry(0)
            return _safe_redirect_or_default(request, next_url, user)

        cache.set(attempt_key, failed_attempts + 1, LOGIN_LOCKOUT_SECONDS)
        logger.warning("Failed login attempt for identifier %s", identifier or "<blank>")
        messages.error(request, "Invalid credentials. Please try again.")

    return render(request, "accounts/login.html", {"next": next_url})


@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, "You have been logged out.")
    return redirect("/accounts/login/")


def csrf_failure(request, reason=""):
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    redirect_url = "/accounts/login/?csrf=expired"
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        redirect_url = f"{redirect_url}&next={next_url}"
    return redirect(redirect_url)


def _safe_redirect_or_default(request, next_url, user):
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return redirect(next_url)

    if user.is_superuser or user.is_staff:
        return redirect("/finance/admin/dashboard/")
    if getattr(user, "role", None) == User.Role.PRODUCER:
        return redirect("/orders/producer/")
    if getattr(user, "role", None) == User.Role.CUSTOMER:
        return redirect("/discover/")
    if getattr(user, "role", None) == User.Role.ADMIN:
        return redirect("/finance/admin/dashboard/")

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
