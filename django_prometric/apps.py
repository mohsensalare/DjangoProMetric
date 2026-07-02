from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PrometricConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "django_prometric"
    verbose_name = _("ProMetric")
