"""Dashboard access control.

Who can open the dashboard is decided by ``DJANGO_PROMETRIC["ACCESS"]``:

- ``"superuser"`` (default) — superusers only
- ``"staff"`` — any staff member
- ``"permission"`` — nobody by default; access is granted explicitly
- a callable or dotted path — full custom logic, receives the user

Independently of the baseline, any user holding the
``django_prometric.view_dashboard`` permission (assignable to users and
groups from the Django admin) is allowed in. With ``STEALTH_404`` enabled,
unauthorized visitors receive a plain 404 so the dashboard URL stays
undiscoverable.
"""

from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import NoReverseMatch, reverse
from django.utils.module_loading import import_string

from .conf import get_config

PERMISSION = "django_prometric.view_dashboard"


def user_can_view(user) -> bool:
    access = get_config()["ACCESS"]
    if callable(access):
        return bool(access(user))
    if not user.is_active:
        return False
    if access == "superuser":
        baseline = user.is_superuser
    elif access == "staff":
        baseline = user.is_staff
    elif access == "permission":
        baseline = False
    else:  # dotted path to a callable
        return bool(import_string(access)(user))
    return baseline or user.has_perm(PERMISSION)


def dashboard_access_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if user_can_view(request.user):
            return view_func(request, *args, **kwargs)
        if get_config()["STEALTH_404"]:
            raise Http404
        if not request.user.is_authenticated:
            try:
                login_url = reverse("admin:login")
            except NoReverseMatch:
                login_url = None  # falls back to settings.LOGIN_URL
            return redirect_to_login(request.get_full_path(), login_url=login_url)
        raise PermissionDenied

    return wrapper
