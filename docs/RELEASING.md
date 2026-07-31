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
   It runs the full test suite first, then builds and publishes.
6. Once published, users can install with:
   ```bash
   uv tool install cosmo-travel-mcp
   ```

## Manual publish (fallback)

If trusted publishing is unavailable, build locally and upload with twine:

```bash
uv build
uv run twine upload dist/*
```

You'll need a PyPI API token scoped to `cosmo-travel-mcp` set as
`TWINE_USERNAME=__token__` and `TWINE_PASSWORD=<token>`.
