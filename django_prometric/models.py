"""Database models.

``Dashboard`` has no table at all — it only anchors the admin entry and the
``view_dashboard`` permission. ``Snapshot`` stores reports the user chose to
keep for later comparison.
"""

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


class Snapshot(models.Model):
    """A report frozen at a point in time so runs can be compared later.

    ``data`` follows the ``providers.base.Report`` schema and ``filters``
    keeps the route selection that was active, so two runs can be compared
    honestly even if the configuration changed in between. Retaking a report
    links the new row to the first one of the series through ``parent``:
    one report observed over several time windows.
    """

    taken_at = models.DateTimeField(auto_now_add=True)
    provider = models.CharField(max_length=50)
    period = models.CharField(max_length=10)
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
