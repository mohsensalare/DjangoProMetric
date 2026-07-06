"""Minimal Django settings for the test suite."""

SECRET_KEY = "test-only-not-secret"

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django_prometric",
]

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "DIRS": [],
        "OPTIONS": {},
    }
]

USE_TZ = True

DJANGO_PROMETRIC = {
    "PROVIDERS": ["postgres"],
}
