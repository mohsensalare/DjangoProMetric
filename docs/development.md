---
title: Development guide
description: Set up a django-prometric development environment, run tests, preview documentation, and understand the project layout.
---

# Development

Clone the repository and install the editable development environment:

```console
git clone https://github.com/mohsensalare/DjangoProMetric.git
cd DjangoProMetric
python -m pip install -e ".[dev,docs]"
```

## Run the checks

```console
python -m pytest
ruff check .
ruff format --check .
```

These match the test and lint checks run in CI.

## Work on the documentation

Start the development server:

```console
mkdocs serve
```

Open `http://127.0.0.1:8000`. Changes to Markdown, `mkdocs.yml`, and the custom
stylesheet reload automatically.

Build the exact production output with strict link and configuration checks:

```console
mkdocs build --strict
```

Every push to `main` publishes the resulting static site to GitHub Pages. Pull
requests build the site but do not deploy it.

## Project layout

```text
django_prometric/   package source, templates, and static assets
docs/               Markdown documentation
example/            runnable Django project
tests/              pytest suite
mkdocs.yml          documentation navigation and theme
```

The version, tag, PyPI Trusted Publishing, and release-branch process is
documented in
[RELEASING.md](https://github.com/mohsensalare/DjangoProMetric/blob/main/RELEASING.md).
