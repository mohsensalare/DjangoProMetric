"""Database models.

``Dashboard`` has no table at all — it only anchors the admin entry and the
``view_dashboard`` permission. ``Snapshot`` stores reports the user chose to
keep for later comparison. ``Preferences`` keeps each user's dashboard
settings.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class Dashboard(models.Model):  # noqa: DJ008 — never instantiated, no __str__ needed
    """Table-less anchor for the admin link and the access permission.

    ``managed = False`` with no fields: it only exists so the dashboard shows
    up as an entry inside the Django admin and so the
    ``django_prometric.view_dashboard`` permission can be assigned to users
    and groups from there.
    """

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [("view_dashboard", "Can view the ProMetric dashboard")]
        verbose_name = _("Analytics dashboard")
        verbose_name_plural = _("Analytics dashboard")


class Preferences(models.Model):
    """One user's dashboard settings, as a single JSON document.

    ``data`` follows this schema (currently version 1)::

        {
            "version": 1,
            "cards": {
                "hidden": ["bots-card-cloudflare", "seo-card-cloudflare"],
                "order": ["traffic-chart-cloudflare", "overview-cards-cloudflare"]
            }
        }

    ``cards.hidden`` lists the slot ids of dashboard cards the user switched
    off in the "Customize" panel; ``cards.order`` is the display order they
    arranged (cards missing from it keep their configured position, after
    the ordered ones). New setting groups get their own top-level key next
    to ``cards``; readers must tolerate keys they don't know.
    """

    SCHEMA_VERSION = 1

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="prometric_preferences",
    )
    data = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("preferences")
        verbose_name_plural = _("preferences")

    def __str__(self):
        return f"preferences of {self.user}"

    @property
    def hidden_cards(self) -> list:
        return self._cards("hidden")

    @property
    def card_order(self) -> list:
        return self._cards("order")

    def _cards(self, key) -> list:
        cards = (self.data or {}).get("cards") or {}
        return [slot for slot in cards.get(key) or [] if isinstance(slot, str)]

    def set_cards(self, hidden, order) -> None:
        """Replace the ``cards`` group of the document."""
        self.data = {
            **(self.data or {}),
            "version": self.SCHEMA_VERSION,
            "cards": {"hidden": sorted(set(hidden)), "order": list(order)},
        }


class Snapshot(models.Model):
    """A report frozen at a point in time so runs can be compared later.

    ``data`` follows the ``providers.base.Report`` schema and ``filters``
    keeps the route selection that was active, so two runs can be compared
    honestly even if the configuration changed in between. Retaking a report
    links the new row to the first one of the series through ``parent``:
    one report observed over several time windows.
    """

    taken_at = models.DateTimeField(auto_now_add=True)
    provider = models.CharField(max_length=100)  # comma-joined provider slugs
    period = models.CharField(max_length=10)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    filters = models.JSONField(default=dict)
    data = models.JSONField(default=dict)
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="retakes",
    )

    class Meta:
        ordering = ["-taken_at"]
        verbose_name = _("snapshot")
        verbose_name_plural = _("snapshots")

    def __str__(self):
        return f"{self.provider} · {self.period} · {self.taken_at:%Y-%m-%d %H:%M}"

    @property
    def series(self):
        """This snapshot's family — first take and its retakes, oldest first."""
        root = self.parent or self
        return [root, *root.retakes.order_by("taken_at")]

    @property
    def reports(self) -> dict:
        """Provider slug → report dict; reads rows of every schema version."""
        data = self.data or {}
        if "reports" in data:
            return data["reports"]
        return {self.provider: data} if data else {}

    @property
    def provider_slugs(self) -> list:
        return self.provider.split(",") if self.provider else []
