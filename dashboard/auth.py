from urllib.parse import urlencode

from allauth.account.models import EmailAddress
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives
from django.core.validators import validate_email
from django.db import transaction
from django.http import HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from sesame.utils import get_parameters
from sesame.views import LoginView as SesameLoginView

from .views import ONBOARDING_SESSION_KEY


def _safe_next(request, value):
    if value and url_has_allowed_host_and_scheme(
        value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return value
    return reverse("onboarding") if request.session.get(ONBOARDING_SESSION_KEY) else reverse(
        "dashboard-all"
    )


def _unique_username(email):
    User = get_user_model()
    base = email[:150]
    candidate = base
    suffix = 2
    while User.objects.filter(username=candidate).exists():
        ending = f"-{suffix}"
        candidate = f"{base[: 150 - len(ending)]}{ending}"
        suffix += 1
    return candidate


def _auth_context(request, *, email="", email_error="", sent=False, auth_error=""):
    details = request.session.get(ONBOARDING_SESSION_KEY)
    next_url = _safe_next(request, request.POST.get("next") or request.GET.get("next"))
    return {
        "website": details,
        "next": next_url,
        "email": email,
        "email_error": email_error,
        "sent": sent,
        "auth_error": auth_error,
        "google_url": f"{reverse('google-start')}?{urlencode({'next': next_url})}",
    }


def signup(request):
    if request.user.is_authenticated:
        return redirect(_safe_next(request, request.GET.get("next")))
    if request.method not in {"GET", "POST"}:
        return HttpResponseNotAllowed(["GET", "POST"])

    auth_error = (
        "That sign-in link is invalid or has expired. Request a new link below."
        if request.GET.get("auth_error") == "invalid-link"
        else "Google sign-in is not configured yet. Use email to continue."
        if request.GET.get("auth_error") == "google-unavailable"
        else ""
    )
    if request.method == "GET":
        return render(request, "registration/signup.html", _auth_context(request, auth_error=auth_error))

    email = request.POST.get("email", "").strip().lower()
    try:
        validate_email(email)
    except ValidationError:
        return render(
            request,
            "registration/signup.html",
            _auth_context(request, email=email, email_error="Enter a valid email address."),
            status=400,
        )

    User = get_user_model()
    with transaction.atomic():
        user = User.objects.filter(email__iexact=email).order_by("pk").first()
        if user is None:
            user = User(username=_unique_username(email), email=email)
            user.set_unusable_password()
            user.save()
        elif user.email != email:
            user.email = email
            user.save(update_fields=["email"])
        EmailAddress.objects.update_or_create(
            user=user,
            email__iexact=email,
            defaults={"email": email, "primary": True},
        )

    next_url = _safe_next(request, request.POST.get("next"))
    parameters = get_parameters(user)
    parameters["next"] = next_url
    login_url = request.build_absolute_uri(
        f"{reverse('sesame-login')}?{urlencode(parameters)}"
    )
    email_context = {
        "login_url": login_url,
        "logo_url": f"{settings.SITEHITS_BASE_URL}{settings.STATIC_URL}sitehits-mark.svg",
    }
    try:
        message = EmailMultiAlternatives(
            "Your SiteHits sign-in link",
            render_to_string("email/magic_link.txt", email_context),
            settings.DEFAULT_FROM_EMAIL,
            [email],
        )
        message.attach_alternative(
            render_to_string("email/magic_link.html", email_context),
            "text/html",
        )
        message.send()
    except Exception:
        return render(
            request,
            "registration/signup.html",
            _auth_context(
                request,
                email=email,
                email_error="We could not send the link. Try again in a moment.",
            ),
            status=503,
        )
    return render(
        request,
        "registration/signup.html",
        _auth_context(request, email=email, sent=True),
    )


def google_start(request):
    next_url = _safe_next(request, request.GET.get("next"))
    if not settings.SITEHITS_GOOGLE_CLIENT_ID or not settings.SITEHITS_GOOGLE_CLIENT_SECRET:
        return redirect(
            f"{reverse('signup')}?{urlencode({'next': next_url, 'auth_error': 'google-unavailable'})}"
        )
    return redirect(
        f"{reverse('google_login')}?{urlencode({'process': 'login', 'next': next_url})}"
    )


class SiteHitsSesameLoginView(SesameLoginView):
    max_age = 600

    def login_failed(self):
        next_url = _safe_next(self.request, self.request.GET.get("next"))
        return redirect(
            f"{reverse('signup')}?{urlencode({'next': next_url, 'auth_error': 'invalid-link'})}"
        )

    def login_success(self):
        EmailAddress.objects.filter(user=self.request.user, email__iexact=self.request.user.email).update(
            verified=True,
            primary=True,
        )
        return super().login_success()
