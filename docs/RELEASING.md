# Releasing cosmo-travel-mcp

This project publishes to PyPI via **trusted publishing** (OpenID Connect from
GitHub Actions). No API token is stored anywhere — GitHub issues a short-lived
OIDC token for the publish job, and PyPI verifies it against the trusted
publisher configuration.

## One-time setup (maintainer)

Only the PyPI project owner (`maththedev42`) needs to do this once.

1. **Create a PyPI account** at https://pypi.org (if you don't have one) and
   verify your email.

2. **Add a pending trusted publisher** for the `cosmo-travel-mcp` project at
   https://pypi.org/manage/account/publishing/:
   - **Owner:** `maththedev42`
   - **Repository:** `cosmo-travel-mcp`
   - **Workflow name:** `publish.yml`
   - **Environment name:** `pypi`

   Leave it as *pending* — it activates the first time a matching workflow
   publishes successfully.

## Per-release steps

1. Bump the version in `pyproject.toml` (`[project] version = "..."`).
2. Update `CHANGELOG.md` with the release notes.
3. Commit both and push:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "chore: bump version to X.Y.Z"
   git push origin main
   ```
4. Tag the release:
   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```
5. Watch the **Publish to PyPI** workflow at
   https://github.com/maththedev42/cosmo-travel-mcp/actions/workflows/publish.yml.
   Three jobs run in order: the full test suite, then build + publish, then the
   GitHub Release.
6. Once published, users can install with:
   ```bash
   uv tool install cosmo-travel-mcp
   ```

### The GitHub Release is automatic

The `github_release` job creates it from the tag, with the body taken from
this version's `CHANGELOG.md` section — so step 2 above is what the release
page ends up saying, and there is nothing to write twice.

It runs **after** `publish` deliberately. A release page for a version that
never reached PyPI would point people at an install command that fails.

`scripts/release_notes.py` does the extraction and **exits non-zero when the
section is missing or empty**, which fails the job rather than publishing a
release with a blank body. To see what a release will say before tagging:

```bash
python3 scripts/release_notes.py v1.2.0
```

Releases for `v1.0.0` … `v1.2.0` were created retroactively with the same
script, so every tag has one.

## Manual publish (fallback)

If trusted publishing is unavailable, build locally and upload with twine:

```bash
uv build
uv run twine upload dist/*
```

You'll need a PyPI API token scoped to `cosmo-travel-mcp` set as
`TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<token>`.
