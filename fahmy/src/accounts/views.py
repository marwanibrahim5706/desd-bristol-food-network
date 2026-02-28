from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.shortcuts import render, redirect
from django.views.decorators.http import require_http_methods
from django.utils.http import url_has_allowed_host_and_scheme
from django.conf import settings

@require_http_methods(["GET", "POST"])
def login_view(request):
    # 1) Respect ?next=... if present
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
            User = get_user_model()
            try:
                u = User.objects.get(email__iexact=identifier)
                user = authenticate(request, username=u.username, password=password)
            except User.DoesNotExist:
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
    # Prevent open redirect attacks
    if next_url and url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return redirect(next_url)

    role = getattr(user, "role", None)
    if role == "producer":
        return redirect("/orders/producer/dashboard/")
    if role == "customer":
        return redirect("/")
    return redirect("/orders/producer/dashboard/")