---
title: Dashboard security
description: Protect django-prometric with Django authentication, permissions, custom access policies, and stealth 404 responses.
---

# Security

The dashboard exposes operational data, route names, traffic patterns, and
possibly error or query details. It should never be anonymously accessible.

## Default policy

Only active superusers can open the dashboard by default:

```python title="settings.py"
DJANGO_PROMETRIC = {
    "ACCESS": "superuser",
}
```

An unauthenticated visitor is sent to the Django admin login when available.
An authenticated user without access receives `403 Forbidden`.

## Available policies

| Value | Who gets baseline access |
| --- | --- |
| `"superuser"` | Active superusers |
| `"staff"` | Active staff users |
| `"permission"` | Nobody until permission is explicitly assigned |
| `"package.module.callable"` | Users accepted by your custom function |

Regardless of the baseline policy, an active user with
`django_prometric.view_dashboard` can access the dashboard. Assign that
permission directly or through a group in Django admin.

## Permission-only access

```python title="settings.py"
DJANGO_PROMETRIC = {
    "ACCESS": "permission",
}
```

This is a good choice when a dedicated operations group should have access but
general staff users should not.

## Custom policy

The callable receives the current user and returns a boolean:

```python title="myproject/permissions.py"
def can_view_metrics(user) -> bool:
    return user.is_active and user.groups.filter(name="Operations").exists()
```

```python title="settings.py"
DJANGO_PROMETRIC = {
    "ACCESS": "myproject.permissions.can_view_metrics",
}
```

## Hide the endpoint

Return a plain 404 to unauthorized visitors so the dashboard URL cannot be
confirmed by probing:

```python title="settings.py"
DJANGO_PROMETRIC = {
    "ACCESS": "permission",
    "STEALTH_404": True,
}
```

## Credential handling

- Read provider secrets from environment variables or a secrets manager.
- Never put API tokens in `DJANGO_PROMETRIC` values committed to source control.
- Grant read-only scopes required by the selected provider.
- Scope edge providers to the host or subdomain served by this Django project.
- Use HTTPS and the same secure session-cookie settings as the rest of the admin
  surface.
