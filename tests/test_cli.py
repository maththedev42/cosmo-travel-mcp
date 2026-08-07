"""Tests for the command-line surface and the onboarding text it shares.

The CLI's most important property is negative: with no arguments it must be a
plain stdio MCP server and print nothing to stdout, because stdout carries the
protocol.
"""

from __future__ import annotations

import pathlib
import subprocess

import pytest
import respx

from cosmo_travel_mcp import cli
from cosmo_travel_mcp.onboarding import (
    MAPS_ENV,
    PACKAGE_NAME,
    REPO_URL,
    SERPAPI_ENV,
    SERPAPI_SIGNUP_URL,
    SERPAPI_TOOLS,
    SERVER_NAME,
    missing_key_message,
    register_argv,
    register_command,
    remove_command,
    install_command,
    launch_argv,
    setup_guide,
)
from cosmo_travel_mcp.tools.setup import (
    ROUTES_API_BASE,
    check_setup,
    SERPAPI_ACCOUNT_URL,
    probe_maps,
    probe_serpapi,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def no_server(monkeypatch):
    """Replace the server runner so dispatch tests never block on stdio."""
    calls: list[int] = []

    def _fake() -> int:
        calls.append(1)
        return 0

    monkeypatch.setattr(cli, "_run_server", _fake)
    return calls


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def test_no_args_runs_the_server(no_server, capsys):
    assert cli.main([]) == 0
    assert len(no_server) == 1
    assert capsys.readouterr().out == ""


def test_unknown_arg_still_serves_and_warns_on_stderr(no_server, capsys):
    """An unrecognised flag must not stop the server, and must not touch stdout."""
    assert cli.main(["--frobnicate"]) == 0
    assert len(no_server) == 1

    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is the MCP protocol channel
    assert "--frobnicate" in captured.err


def test_setup_does_not_start_the_server(no_server, capsys):
    assert cli.main(["setup"]) == 0
    assert no_server == []


def test_version_prints_and_does_not_serve(no_server, capsys):
    assert cli.main(["--version"]) == 0
    assert no_server == []
    assert "cosmo-travel-mcp" in capsys.readouterr().out


def test_help_lists_setup(no_server, capsys):
    assert cli.main(["--help"]) == 0
    assert no_server == []
    out = capsys.readouterr().out
    assert "setup --register" in out


# ---------------------------------------------------------------------------
# The printed guide
# ---------------------------------------------------------------------------


def test_guide_covers_both_keys_and_how_to_register(capsys):
    cli.main(["setup"])
    out = capsys.readouterr().out

    # Where the keys come from.
    assert SERPAPI_SIGNUP_URL in out
    assert "console.cloud.google.com" in out
    assert "Routes API" in out
    assert "100 searches/month" in out

    # How to attach them — including the already-registered path.
    assert f"claude mcp add {SERVER_NAME}" in out
    assert remove_command() in out
    assert "setup --register" in out
    assert "check_setup" in out


def test_guide_names_the_env_vars(capsys):
    cli.main(["setup"])
    out = capsys.readouterr().out
    assert SERPAPI_ENV in out
    assert MAPS_ENV in out


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------


def test_register_argv_is_execvp_ready():
    argv = register_argv(serpapi_key="sk-abc", maps_key="AIza-xyz")

    assert argv[:4] == ["claude", "mcp", "add", SERVER_NAME]
    assert "--scope" in argv and argv[argv.index("--scope") + 1] == "user"
    assert f"{SERPAPI_ENV}=sk-abc" in argv
    assert f"{MAPS_ENV}=AIza-xyz" in argv
    # Everything after the bare `--` is the launch command, not flags for claude.
    assert argv[argv.index("--") + 1 :] == launch_argv()


def test_register_argv_omits_keys_that_were_skipped():
    argv = register_argv(serpapi_key="sk-abc", maps_key=None)
    assert not any(a.startswith(f"{MAPS_ENV}=") for a in argv)
    assert f"{SERPAPI_ENV}=sk-abc" in argv


def test_register_argv_honours_scope_and_name():
    argv = register_argv(serpapi_key="k", scope="project", name="travel")
    assert argv[3] == "travel"
    assert argv[argv.index("--scope") + 1] == "project"


def test_register_command_uses_placeholders_not_secrets():
    cmd = register_command()
    assert f"-e {SERPAPI_ENV}=<your-serpapi-key>" in cmd
    assert f"-e {MAPS_ENV}=<your-google-maps-key>" in cmd


def test_registration_launches_an_installed_binary_not_uvx():
    """`uvx --from git+…` re-resolves on every launch and blows the client's
    30s stdio startup timeout — measured at >120s cold. Registration must
    point at something already installed."""
    argv = register_argv(serpapi_key="k")
    cmd = register_command()
    for surface in (" ".join(argv), cmd):
        assert "uvx" not in surface
        assert f"git+{REPO_URL}" not in surface
    assert argv[-1] == PACKAGE_NAME


def test_install_command_installs_from_pypi():
    assert install_command() == f"uv tool install {PACKAGE_NAME}"


def test_register_argv_accepts_an_absolute_binary_path():
    argv = register_argv(serpapi_key="k", binary="/opt/bin/cosmo-travel-mcp")
    assert argv[-1] == "/opt/bin/cosmo-travel-mcp"


def test_own_binary_resolves_to_an_absolute_path(monkeypatch, tmp_path):
    """A client spawning the server may not inherit the shell PATH."""
    real = tmp_path / "real" / PACKAGE_NAME
    real.parent.mkdir()
    real.write_text("#!/bin/sh\n")
    link = tmp_path / PACKAGE_NAME
    link.symlink_to(real)

    monkeypatch.setattr(cli.shutil, "which", lambda _: str(link))
    assert cli.own_binary() == str(real)  # symlink resolved


# ---------------------------------------------------------------------------
# Secret handling
# ---------------------------------------------------------------------------


def test_mask_never_shows_the_whole_key():
    secret = "sk-1234567890abcdef"
    masked = cli.mask(secret)
    assert secret not in masked
    assert masked.startswith("sk-1")
    assert masked.endswith("cdef")


def test_mask_hides_short_keys_entirely():
    """A short key would be fully reconstructable from head+tail — star it all."""
    assert set(cli.mask("shortkey")) == {"*"}


def test_mask_handles_empty():
    assert cli.mask("") == "(none)"


def test_register_refuses_without_a_terminal(monkeypatch, capsys):
    """Prompting for secrets requires a tty; otherwise print the manual command."""

    def _explode(*a, **k):  # pragma: no cover - must not be reached
        raise AssertionError("subprocess must not run without a tty")

    monkeypatch.setattr(subprocess, "run", _explode)
    monkeypatch.setattr(cli, "_is_interactive", lambda: False)

    assert cli.main(["setup", "--register"]) == 2
    err = capsys.readouterr().err
    assert "interactive terminal" in err
    assert "claude mcp add" in err  # still tells them what to run by hand


def test_register_reports_missing_claude_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_is_interactive", lambda: True)
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)

    assert cli.main(["setup", "--register"]) == 2
    err = capsys.readouterr().err
    assert "not on PATH" in err
    assert "claude mcp add" in err


# ---------------------------------------------------------------------------
# Existing-registration detection
# ---------------------------------------------------------------------------


def test_is_registered_true_on_exit_zero(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    assert cli.is_registered() is True


def test_is_registered_false_on_exit_one(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: "/usr/bin/claude")
    monkeypatch.setattr(
        cli.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""),
    )
    assert cli.is_registered() is False


def test_is_registered_false_when_cli_absent(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    assert cli.is_registered() is False


# ---------------------------------------------------------------------------
# Key probes (shared by check_setup and `setup --register`)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_serpapi_accepts_a_good_key():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json={"plan_searches_left": 42})
        result = await probe_serpapi("good-key")

    assert result["ok"] is True
    assert result["account"]["plan_searches_left"] == 42


@pytest.mark.asyncio
async def test_probe_serpapi_rejects_a_bad_key():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(json={"error": "Invalid API key"})
        result = await probe_serpapi("bad-key")

    assert result["ok"] is False
    assert "Invalid API key" in result["reason"]


@pytest.mark.asyncio
async def test_probe_serpapi_distinguishes_outage_from_bad_key():
    with respx.mock as mock:
        mock.get(SERPAPI_ACCOUNT_URL).respond(status_code=503)
        result = await probe_serpapi("some-key")

    assert result["ok"] is False
    assert "503" in result["reason"]
    assert "may be fine" in result["reason"]
    # A single HTTP prefix, not "(HTTP HTTP 503)".
    assert "HTTP HTTP" not in result["reason"]


@pytest.mark.asyncio
async def test_probe_maps_accepts_a_good_key():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(
            json=[{"originIndex": 0, "status": {}, "distanceMeters": 1}]
        )
        result = await probe_maps("good-key")

    assert result["ok"] is True


@pytest.mark.asyncio
async def test_probe_maps_rejects_a_denied_key():
    with respx.mock as mock:
        mock.post(ROUTES_API_BASE).respond(status_code=403, json={})
        result = await probe_maps("bad-key")

    assert result["ok"] is False
    assert "403" in result["reason"]
    assert "Routes API may not be enabled" in result["reason"]


# ---------------------------------------------------------------------------
# Missing-key errors
#
# A model that calls a tool without calling check_setup first sees only the
# exception text, so that text has to be self-sufficient.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env", [SERPAPI_ENV, MAPS_ENV])
def test_missing_key_message_is_self_sufficient(env):
    msg = missing_key_message(env)

    assert env in msg
    assert "is not set" in msg  # anchor other tests already match on
    assert "check_setup" in msg
    assert "setup --register" in msg
    # The trap worth naming explicitly: shell export does nothing for an MCP
    # server, so advice to export sends the user to fix the wrong thing.
    assert "exporting the variable in a shell has no effect" in msg
    # "see the README" is unreachable from inside a chat client.
    assert "README" not in msg


def test_missing_key_message_points_at_the_right_provider():
    assert SERPAPI_SIGNUP_URL in missing_key_message(SERPAPI_ENV)
    assert "console.cloud.google.com" in missing_key_message(MAPS_ENV)
    assert "Routes API" in missing_key_message(MAPS_ENV)
    # No cross-contamination: a missing flights key must not send the user to
    # Google Cloud.
    assert "console.cloud.google.com" not in missing_key_message(SERPAPI_ENV)


def test_key_getters_raise_the_shared_message(monkeypatch):
    """Both gated code paths use the same text — no per-module wording."""
    from cosmo_travel_mcp.tools.driving import _get_maps_api_key
    from cosmo_travel_mcp.tools.flights import _get_api_key

    monkeypatch.delenv(SERPAPI_ENV, raising=False)
    monkeypatch.delenv(MAPS_ENV, raising=False)

    with pytest.raises(ValueError) as serpapi_exc:
        _get_api_key()
    assert str(serpapi_exc.value) == missing_key_message(SERPAPI_ENV)

    with pytest.raises(ValueError) as maps_exc:
        _get_maps_api_key()
    assert str(maps_exc.value) == missing_key_message(MAPS_ENV)


# ---------------------------------------------------------------------------
# Drift guards — three surfaces must keep telling the same story
# ---------------------------------------------------------------------------


def test_tool_guide_and_cli_share_one_command():
    """The MCP tool's `setup` field must not drift from the CLI's command."""
    guide = setup_guide(need_serpapi=True, need_maps=True)
    assert f"claude mcp add {SERVER_NAME} --scope user" in guide
    assert remove_command() in guide
    assert install_command() in guide


def test_readme_documents_the_same_registration_command():
    readme = (REPO_ROOT / "README.md").read_text()
    assert f"claude mcp add {SERVER_NAME} --scope user" in readme
    assert f"-e {SERPAPI_ENV}=" in readme
    assert install_command() in readme


def test_getting_keys_doc_exists_and_covers_both_providers():
    doc = (REPO_ROOT / "docs" / "GETTING_KEYS.md").read_text()
    assert SERPAPI_SIGNUP_URL in doc
    assert "console.cloud.google.com" in doc
    assert "Routes API" in doc


@pytest.mark.asyncio
async def test_serpapi_tools_lists_every_serpapi_gated_tool(monkeypatch):
    """The whole set at once, derived from `check_setup` rather than transcribed.

    The per-tool drift tests below only catch a tool somebody remembered to
    write one for. `search_things_to_do` shipped without one and was missing
    from `SERPAPI_TOOLS` across three releases; `search_car_rentals` was added
    the same way. Naming each tool by hand is the thing that failed, so this
    compares the constant against what `check_setup` actually gates.

    With neither key set, `check_setup` returns its reasons without touching
    the network, so the SerpAPI-gated group is readable straight off the
    result.
    """
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_MAPS_API_KEY", raising=False)

    result = await check_setup()
    gated = {
        t["tool"] for t in result["tools"] if SERPAPI_ENV in t.get("reason", "")
    }

    assert gated == set(SERPAPI_TOOLS), (
        "SERPAPI_TOOLS and check_setup disagree about which tools need a "
        f"SerpAPI key — only in check_setup: {sorted(gated - set(SERPAPI_TOOLS))}, "
        f"only in SERPAPI_TOOLS: {sorted(set(SERPAPI_TOOLS) - gated)}"
    )


def test_onboarding_and_readme_drift_new_get_accommodation_details():
    """README and SERPAPI_TOOLS must both include get_accommodation_details."""
    readme = (REPO_ROOT / "README.md").read_text()

    assert "get_accommodation_details" in readme, (
        "README.md must mention get_accommodation_details"
    )
    assert "get_accommodation_details" in SERPAPI_TOOLS, (
        "SERPAPI_TOOLS must include get_accommodation_details"
    )


def test_onboarding_and_readme_drift_new_search_events():
    """README tools table and onboarding must stay in sync for search_events."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert "`search_events`" in readme
    # setup_guide uses human labels (e.g. "events") not tool function names.
    guide = setup_guide(need_serpapi=True, need_maps=False)
    assert "events" in guide


# ---------------------------------------------------------------------------
# Multi-client setup --client flag
# ---------------------------------------------------------------------------


def test_setup_client_claude_code_shows_register_command(capsys):
    """claude-code is the default and shows the claude mcp add command."""
    assert cli.main(["setup", "--client", "claude-code"]) == 0
    out = capsys.readouterr().out
    assert "claude mcp add" in out
    assert SERVER_NAME in out


@pytest.mark.parametrize(
    "client_name,expected_key",
    [
        ("claude-desktop", "mcpServers"),
        ("cursor", "mcpServers"),
        ("windsurf", "mcpServers"),
        ("cline", "mcpServers"),
        ("vscode", "servers"),
    ],
)
def test_setup_client_shows_json_snippet_with_right_key(
    client_name, expected_key, capsys
):
    """Each non-claude-code client shows a JSON block with its top-level key."""
    assert cli.main(["setup", "--client", client_name]) == 0
    out = capsys.readouterr().out
    assert f'"{expected_key}"' in out
    assert SERVER_NAME in out


def test_setup_client_snippet_contains_absolute_binary_path(capsys, monkeypatch):
    """The command in the snippet should be an absolute path, not a bare name."""
    monkeypatch.setattr(cli, "own_binary", lambda: "/usr/local/bin/cosmo-travel-mcp")
    assert cli.main(["setup", "--client", "cursor"]) == 0
    out = capsys.readouterr().out
    assert '"/usr/local/bin/cosmo-travel-mcp"' in out


@pytest.mark.parametrize("client_name", ["cursor", "claude-desktop", "windsurf", "cline", "vscode"])
def test_setup_client_uses_placeholders_not_real_keys(
    client_name, monkeypatch, capsys
):
    """When keys are set in the environment, the printed snippet must still
    show placeholders — never real key values in plain output."""
    monkeypatch.setenv(SERPAPI_ENV, "sk-real-serpapi-key-12345")
    monkeypatch.setenv(MAPS_ENV, "AIza-real-maps-key-67890")
    assert cli.main(["setup", "--client", client_name]) == 0
    out = capsys.readouterr().out
    assert "sk-real-serpapi-key-12345" not in out
    assert "AIza-real-maps-key-67890" not in out
    assert "<your-serpapi-key>" in out
    assert "<your-google-maps-key>" in out


def test_setup_guide_now_mentions_other_clients(capsys):
    """Running `setup` (no flags) should nudge towards --client for
    non-Claude-Code users."""
    assert cli.main(["setup"]) == 0
    out = capsys.readouterr().out
    assert "different MCP client" in out
    assert "setup --client" in out


def test_setup_guide_for_check_setup_mentions_other_clients():
    """The `setup` field returned by check_setup (via setup_guide) should
    also mention other clients."""
    guide = setup_guide(need_serpapi=True, need_maps=False)
    assert "non-Claude-Code clients" in guide.lower() or "other than claude code" in guide.lower()
    assert "setup --client" in guide


def test_unknown_client_reports_error(capsys):
    """An unknown --client value should error with valid names."""
    # argparse prints the error to stderr and exits, but our main catches
    # SystemExit from argparse and returns 2.
    rc = cli.main(["setup", "--client", "no-such-client"])
    assert rc != 0
    err = capsys.readouterr().err
    assert "invalid choice" in err.lower() or "no-such-client" in err


def test_client_list_includes_all_six(capsys):
    """Smoke test: all six clients produce output without errors."""
    from cosmo_travel_mcp.onboarding import CLIENT_CONFIGS

    for name in CLIENT_CONFIGS:
        rc = cli.main(["setup", "--client", name])
        assert rc == 0
        out = capsys.readouterr().out
        assert len(out) > 50  # something substantial printed
