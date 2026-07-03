"""The admin entry: a single "Analytics dashboard" item that opens the page."""

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import NoReverseMatch, reverse
from django.utils.translation import gettext as _

from .models import Dashboard
from .permissions import user_can_view


@admin.register(Dashboard)
class DashboardAdmin(admin.ModelAdmin):
    """Not a real changelist — clicking the entry opens the dashboard."""

    def has_module_permission(self, request):
        return user_can_view(request.user)

    def has_view_permission(self, request, obj=None):
        return user_can_view(request.user)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        try:
            url = reverse("django_prometric:dashboard")
        except NoReverseMatch:
            self.message_user(
                request,
                _('Include "django_prometric.urls" in your URLconf to open the dashboard.'),
                messages.WARNING,
            )
            return redirect("admin:index")
        return redirect(url)
