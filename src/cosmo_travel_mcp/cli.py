"""Command-line surface for cosmo-travel-mcp.

With no arguments this runs the MCP server over stdio — that is how an MCP
client launches it, and nothing here may write to stdout in that mode, because
stdout *is* the protocol channel.

``cosmo-travel-mcp setup`` prints the key-acquisition guide.
``cosmo-travel-mcp setup --register`` prompts for the keys, validates them
against the real APIs, and registers the server with Claude Code.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import shutil
import subprocess
import sys

from .onboarding import (
    MAPS_ENV,
    PACKAGE_NAME,
    SERPAPI_ENV,
    SERVER_NAME,
    maps_instructions,
    register_argv,
    register_command,
    remove_command,
    serpapi_instructions,
)

_USAGE = f"""\
{PACKAGE_NAME} — travel-planning tools for MCP clients

usage:
  {PACKAGE_NAME}                     run the MCP server over stdio
  {PACKAGE_NAME} setup               show how to get the API keys
  {PACKAGE_NAME} setup --register    get keys, verify them, register the server
  {PACKAGE_NAME} --version           print the version

The bare command is what an MCP client runs; you normally do not run it by
hand. Start with `{PACKAGE_NAME} setup`.
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def mask(secret: str) -> str:
    """Render a key safely for display: ``abcd…wxyz``.

    Enough to recognise which key is in play, not enough to use.
    """
    if not secret:
        return "(none)"
    if len(secret) <= 12:
        return "*" * len(secret)
    return f"{secret[:4]}…{secret[-4:]}"


def _is_interactive() -> bool:
    """Whether we can prompt for secrets.

    Wrapped rather than calling ``sys.stdin.isatty()`` inline so it stays
    patchable, and so a closed or replaced stdin raises nothing.
    """
    try:
        return sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def _rule(title: str) -> str:
    return f"\n{title}\n{'─' * len(title)}"


def _numbered(steps: list[str]) -> str:
    return "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1))


def is_registered(name: str = SERVER_NAME) -> bool:
    """Whether Claude Code already knows a server by this name.

    ``claude mcp get <name>`` exits 0 when the server exists and 1 when it does
    not. If the CLI is missing entirely we report False and the caller falls
    back to printing commands for the user to run.
    """
    if shutil.which("claude") is None:
        return False
    try:
        proc = subprocess.run(
            ["claude", "mcp", "get", name],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# setup (no side effects)
# ---------------------------------------------------------------------------


def print_guide(*, scope: str = "user", name: str = SERVER_NAME) -> None:
    """Print the full key-acquisition guide plus the registration command."""
    print(f"{PACKAGE_NAME} needs up to two API keys. Both have a free tier.")

    print(_rule(f"1. SerpAPI key  →  ${SERPAPI_ENV}"))
    print("  Unlocks: flights, multi-city, accommodations, cheapest-dates.")
    print(_numbered(serpapi_instructions()))

    print(_rule(f"2. Google Maps key  →  ${MAPS_ENV}"))
    print("  Unlocks: drive-or-fly comparison. Skip it if you only want flights.")
    print(_numbered(maps_instructions()))

    print(_rule("3. Register the server"))
    print("  Interactive — prompts for the keys, checks them, registers:")
    print(f"    {PACKAGE_NAME} setup --register\n")
    print("  Or by hand:")
    print(f"    {register_command(scope=scope, name=name, indent='      ')}\n")
    print(
        "  Env vars are fixed at registration time, so an already-registered\n"
        f"  server has to be replaced:\n"
        f"    {remove_command(scope=scope, name=name)}\n"
        "    …then run the add command above."
    )

    print(_rule("4. Verify"))
    print(
        "  Restart the MCP client and ask it to call `check_setup`. It reports\n"
        "  which tools are ready and how much SerpAPI quota is left."
    )


# ---------------------------------------------------------------------------
# setup --register (mutates the user's MCP config)
# ---------------------------------------------------------------------------


def _prompt_key(env_name: str, label: str, where: str) -> str | None:
    """Get one key from the environment or the user. Blank input means skip."""
    existing = os.environ.get(env_name, "")
    if existing:
        answer = input(
            f"Found {env_name} in this shell ({mask(existing)}). Use it? [Y/n] "
        ).strip().lower()
        if answer in ("", "y", "yes"):
            return existing

    print(f"\n{label}")
    print(f"  Get one at: {where}")
    print("  Input is hidden. Press Enter to skip this key.")
    typed = getpass.getpass(f"  {env_name}: ").strip()
    return typed or None


async def _validate(serpapi_key: str | None, maps_key: str | None) -> bool:
    """Check each supplied key against its real API. True if none failed."""
    from .tools.setup import probe_maps, probe_serpapi

    ok = True

    if serpapi_key:
        print("\nChecking the SerpAPI key (free, no quota used)…")
        probe = await probe_serpapi(serpapi_key)
        if probe["ok"]:
            left = probe["account"].get("plan_searches_left", "?")
            print(f"  ✓ valid — {left} searches left this month")
        else:
            print(f"  ✗ {probe['reason']}")
            ok = False

    if maps_key:
        print("\nChecking the Google Maps key (one real Routes API call)…")
        probe = await probe_maps(maps_key)
        if probe["ok"]:
            print("  ✓ valid — Routes API reachable")
        else:
            print(f"  ✗ {probe['reason']}")
            ok = False

    return ok


def _run(argv: list[str], *, what: str) -> bool:
    """Run a claude CLI command, echoing failures."""
    try:
        proc = subprocess.run(argv, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"  ✗ {what} failed: {exc}")
        return False
    if proc.returncode != 0:
        print(f"  ✗ {what} exited {proc.returncode}")
        return False
    return True


def register_flow(*, scope: str, name: str, assume_yes: bool) -> int:
    """Prompt for keys, validate them, and register the server."""
    if not _is_interactive():
        print(
            "setup --register needs an interactive terminal (it prompts for "
            "secrets).\nRun it in a shell, or register by hand:\n\n"
            f"{register_command(scope=scope, name=name)}",
            file=sys.stderr,
        )
        return 2

    if shutil.which("claude") is None:
        print(
            "The `claude` CLI is not on PATH, so this command cannot register "
            "the server for you.\nInstall Claude Code, or run:\n\n"
            f"{register_command(scope=scope, name=name)}",
            file=sys.stderr,
        )
        return 2

    print(f"Registering `{name}` with Claude Code (scope: {scope}).")
    print(f"Need a key? Run `{PACKAGE_NAME} setup` for the full walk-through.\n")

    serpapi_key = _prompt_key(
        SERPAPI_ENV,
        "SerpAPI key — flights, hotels, cheapest-dates.",
        "https://serpapi.com/users/sign_up  (free: 100 searches/month)",
    )
    maps_key = _prompt_key(
        MAPS_ENV,
        "Google Maps key — drive-or-fly comparison. Optional.",
        "https://console.cloud.google.com/  (enable the Routes API)",
    )

    if not serpapi_key and not maps_key:
        print(
            "\nNo keys given — every tool would report NOT ready. Nothing done.",
            file=sys.stderr,
        )
        return 1

    if not asyncio.run(_validate(serpapi_key, maps_key)) and not assume_yes:
        answer = input(
            "\nAt least one key failed its check. Register anyway? [y/N] "
        ).strip().lower()
        if answer not in ("y", "yes"):
            print("Nothing done.")
            return 1

    already = is_registered(name)

    print(_rule("About to run"))
    if already:
        print(f"  {remove_command(scope=scope, name=name)}")
        print(f"  ({name} is already registered; env vars are fixed at")
        print("   registration time, so it has to be replaced)")
    print(
        "  "
        + register_command(
            scope=scope,
            name=name,
            serpapi=bool(serpapi_key),
            maps=bool(maps_key),
            serpapi_value=mask(serpapi_key or ""),
            maps_value=mask(maps_key or ""),
            indent="      ",
        )
    )
    print("  (keys shown masked above; the real values are what get written)")

    if not assume_yes:
        answer = input("\nProceed? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            print("Nothing done.")
            return 1

    if already and not _run(
        ["claude", "mcp", "remove", name, "--scope", scope],
        what="claude mcp remove",
    ):
        return 1

    if not _run(
        register_argv(
            serpapi_key=serpapi_key,
            maps_key=maps_key,
            scope=scope,
            name=name,
        ),
        what="claude mcp add",
    ):
        return 1

    print(f"\n✓ Registered `{name}`.")
    print("  Restart your MCP client, then ask it to call `check_setup`.")
    if not maps_key:
        print(
            f"  No Maps key set — compare_drive_or_fly will report NOT ready.\n"
            f"  Add it later by re-running `{PACKAGE_NAME} setup --register`."
        )
    return 0


def cmd_setup(args: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog=f"{PACKAGE_NAME} setup",
        description="Show how to get the API keys, or register this server.",
    )
    parser.add_argument(
        "--register",
        action="store_true",
        help="prompt for keys, verify them, and run `claude mcp add`",
    )
    parser.add_argument(
        "--scope",
        default="user",
        choices=["user", "project", "local"],
        help="Claude Code config scope to register into (default: user)",
    )
    parser.add_argument(
        "--name",
        default=SERVER_NAME,
        help=f"server name in the MCP config (default: {SERVER_NAME})",
    )
    parser.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="skip confirmation prompts (still prompts for the keys)",
    )
    opts = parser.parse_args(args)

    if opts.register:
        return register_flow(
            scope=opts.scope, name=opts.name, assume_yes=opts.yes
        )

    print_guide(scope=opts.scope, name=opts.name)
    return 0


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _run_server() -> int:
    from .server import main as server_main

    server_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. No arguments means: be an MCP server."""
    argv = list(sys.argv[1:] if argv is None else argv)

    if not argv:
        return _run_server()

    head = argv[0]
    if head in ("setup", "--setup"):
        return cmd_setup(argv[1:])
    if head in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    if head in ("-V", "--version"):
        from .server import __version__

        print(f"{PACKAGE_NAME} {__version__}")
        return 0

    # Unknown argument. Refusing to start would break the server for a client
    # that passes a flag we have not seen, so warn on stderr — never stdout,
    # which carries the MCP protocol — and serve anyway.
    print(
        f"{PACKAGE_NAME}: ignoring unrecognised argument(s): "
        f"{' '.join(argv)}. Starting the server. "
        f"Run `{PACKAGE_NAME} setup` for onboarding.",
        file=sys.stderr,
    )
    return _run_server()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
