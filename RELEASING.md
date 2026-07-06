# Development and release process

This document defines how changes move from development to a public
django-prometric release. The goals are reproducible builds, an auditable
history, immutable releases, and secretless publication to PyPI.

## Overview

```text
feature/* ── pull request ──> develop
                                │
                                ├── release/x.y.z ── pull request ──> main
                                │                                      │
                                │                                  tag vx.y.z
                                │                                      │
                                └<──────── merge main back ─────────────┘
                                                                       │
                                                test → build → approve → publish
                                                              │              │
                                                        GitHub Release      PyPI
```

A push to a development or release branch runs CI and produces a temporary
build artifact. It does **not** publish to PyPI. Only a version tag matching
`v*.*.*` can start the public release workflow.

## Branches

| Branch | Purpose | Created from | Merges into |
| --- | --- | --- | --- |
| `main` | Stable, released code | — | — |
| `develop` | Integration branch for the next release | `main` | `main` through `release/*` |
| `feature/<name>` | One feature or focused change | `develop` | `develop` |
| `release/<version>` | Version, changelog, and final stabilization | `develop` | `main`, then back to `develop` |
| `hotfix/<version>` | Urgent correction to a published release | `main` | `main` and `develop` |

Do not commit directly to `main` or `develop`. Open a pull request and wait for
the required checks. Force-pushing and deleting either protected branch should
be disabled in the GitHub repository rules.

## Pull requests and CI

Every push and pull request should run the public CI workflow. It must:

1. run Ruff;
2. test the supported Python and Django combinations;
3. build both the wheel and source distribution;
4. run `twine check --strict` on the distributions; and
5. upload the distributions as GitHub Actions artifacts.

The workflow logs and artifacts provide evidence for every candidate build.
Artifacts created by normal CI runs are previews only and must never be
uploaded to PyPI.

The automation is expected to live in:

- `.github/workflows/ci.yml` for pushes and pull requests;
- `.github/workflows/release.yml` for `v*.*.*` tags.

Until those files and the GitHub branch rules exist, this document describes
the required policy but does not enforce it automatically.

## Versioning

django-prometric follows [Semantic Versioning](https://semver.org/):

- `MAJOR`: incompatible public API or configuration changes;
- `MINOR`: backward-compatible features;
- `PATCH`: backward-compatible fixes.

The canonical version is `django_prometric.__version__` in
`django_prometric/__init__.py`. A release tag must be the same version prefixed
with `v`:

```text
Package version: 0.2.0
Git tag:         v0.2.0
Release branch:  release/0.2.0
```

The release workflow must fail before publishing if the tag and package
version differ.

## Preparing a release

Start from an up-to-date `develop` branch:

```console
git switch develop
git pull --ff-only
git switch -c release/0.2.0
```

On the release branch:

1. set `__version__` in `django_prometric/__init__.py`;
2. move the relevant entries from `Unreleased` into the new version section in
   `CHANGELOG.md`;
3. confirm that README and provider documentation match the release;
4. run the local checks; and
5. push the branch and open a pull request into `main`.

```console
python -m pytest
ruff check .
python -m build
python -m twine check --strict dist/*

git push -u origin release/0.2.0
```

Use a normal merge commit for release pull requests so the release boundary
remains visible in Git history. Do not tag the release branch before it has
been reviewed and merged into `main`.

## Publishing a release

After the release pull request is merged and the `main` checks pass, create an
annotated tag on the release commit:

```console
git switch main
git pull --ff-only
git tag -a v0.2.0 -m "Release 0.2.0"
git push origin v0.2.0
```

Pushing the tag starts the release workflow. The workflow must:

1. verify that the tag matches `django_prometric.__version__`;
2. rerun tests and static checks from the tagged commit;
3. build the wheel and source distribution once;
4. validate their metadata;
5. retain the distributions as a workflow artifact;
6. wait for approval from the protected `pypi` GitHub Environment;
7. publish the exact artifacts to PyPI through Trusted Publishing; and
8. create a GitHub Release for the tag with the wheel and source distribution
   attached.

After a successful release, merge `main` back into `develop` so the version and
changelog commits remain synchronized:

```console
git switch develop
git pull --ff-only
git merge --no-ff main
git push origin develop
```

## Trusted Publishing

PyPI publication uses OpenID Connect (OIDC), not a long-lived API token. The
release job receives only `id-token: write`; PyPI exchanges that identity for a
short-lived publishing credential.

Create a protected GitHub Environment named `pypi` and require manual approval
for deployments. Register the PyPI pending publisher with these exact values:

| Field | Value |
| --- | --- |
| PyPI project name | `django-prometric` |
| GitHub owner | `mohsensalare` |
| GitHub repository | `DjangoProMetric` |
| Workflow file | `release.yml` |
| Environment | `pypi` |

No `PYPI_TOKEN` secret should be added to GitHub. GitHub's automatically
generated `GITHUB_TOKEN` and OIDC credential are temporary and scoped to the
workflow run.

References:

- [Creating a PyPI project through OIDC](https://docs.pypi.org/trusted-publishers/creating-a-project-through-oidc/)
- [Publishing with a Trusted Publisher](https://docs.pypi.org/trusted-publishers/using-a-publisher/)
- [PyPA GitHub Actions publishing guide](https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/)

## Release history and downloads

Every published version remains available from three public records:

- the Git tag preserves the exact source commit;
- the GitHub Release contains release notes, wheel, and source distribution;
- PyPI retains the version metadata and distribution files used by `pip`.

Never move or recreate a published tag. Never attempt to overwrite an existing
PyPI version; PyPI release files are immutable. If a release is defective,
yank it on PyPI, document the reason, and publish a new patch version. Keep the
GitHub Release and tag as part of the audit history.

## Hotfixes

Create urgent fixes from `main`:

```console
git switch main
git pull --ff-only
git switch -c hotfix/0.2.1
```

Update the patch version and changelog, open a pull request into `main`, and
publish the resulting merge commit with a new tag. Merge `main` back into
`develop` after the hotfix is released.

## Public audit trail

Because the repository is public, anyone can inspect:

- commits, branches, tags, and pull requests;
- CI and release workflow definitions;
- workflow runs, logs, approvals, and build results;
- GitHub Release notes and attached artifacts; and
- every version and distribution file published on PyPI.

Credentials, environment secrets, and provider tokens must never be committed,
printed by scripts, or passed through workflow command-line arguments.
