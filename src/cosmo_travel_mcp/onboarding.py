"""Canonical onboarding text, URLs, and registration commands.

One source of truth for three surfaces that must agree: the ``check_setup`` MCP
tool, the ``cosmo-travel-mcp setup`` CLI, and the README. Each of those used to
spell out registration independently, and they had already drifted once — the
tool told users to pass ``-e`` on ``claude mcp add`` without mentioning that a
server which is *already* registered has to be removed first.
"""

from __future__ import annotations

SERVER_NAME = "cosmo-travel"
PACKAGE_NAME = "cosmo-travel-mcp"
REPO_URL = "https://github.com/maththedev42/cosmo-travel-mcp"

SERPAPI_SIGNUP_URL = "https://serpapi.com/users/sign_up"
SERPAPI_DASHBOARD_URL = "https://serpapi.com/manage-api-key"
GOOGLE_CONSOLE_URL = "https://console.cloud.google.com/"
ROUTES_API_LIBRARY_URL = (
    "https://console.cloud.google.com/apis/library/routes.googleapis.com"
)

SERPAPI_ENV = "SERPAPI_API_KEY"
MAPS_ENV = "GOOGLE_MAPS_API_KEY"

SERPAPI_PLACEHOLDER = "<your-serpapi-key>"
MAPS_PLACEHOLDER = "<your-google-maps-key>"

#: Searches included in SerpAPI's free tier, per month.
FREE_TIER_SEARCHES = 100

SERPAPI_TOOLS = (
    "search_flights",
    "search_multi_city",
    "search_accommodations",
    "get_accommodation_details",
    "search_cheapest_dates",
    "search_events",
)
MAPS_TOOLS = ("compare_drive_or_fly",)


def install_command() -> str:
    """Install this server as a standalone tool (PyPI, post-v1.0)."""
    return f"uv tool install {PACKAGE_NAME}"


def install_from_source_command() -> str:
    """Install from GitHub (until first PyPI release, or for development)."""
    return f"uv tool install git+{REPO_URL}"


def launch_argv(binary: str = PACKAGE_NAME) -> list[str]:
    """The command an MCP client runs to launch this server.

    ``binary`` should be an absolute path: a client spawns the server without
    necessarily inheriting the shell PATH that makes the bare name resolvable.
    """
    return [binary]


def register_argv(
    *,
    serpapi_key: str | None = None,
    maps_key: str | None = None,
    scope: str = "user",
    name: str = SERVER_NAME,
    binary: str = PACKAGE_NAME,
) -> list[str]:
    """Build the ``claude mcp add`` argv, ready for ``subprocess.run``.

    Keys are passed as real values here; use :func:`register_command` for
    anything that gets printed, so secrets do not end up on screen or in shell
    history.
    """
    argv = ["claude", "mcp", "add", name, "--scope", scope]
    if serpapi_key:
        argv += ["-e", f"{SERPAPI_ENV}={serpapi_key}"]
    if maps_key:
        argv += ["-e", f"{MAPS_ENV}={maps_key}"]
    argv += ["--", *launch_argv(binary)]
    return argv


def register_command(
    *,
    serpapi: bool = True,
    maps: bool = True,
    scope: str = "user",
    name: str = SERVER_NAME,
    serpapi_value: str = SERPAPI_PLACEHOLDER,
    maps_value: str = MAPS_PLACEHOLDER,
    binary: str = PACKAGE_NAME,
    indent: str = "  ",
) -> str:
    """Render the ``claude mcp add`` command as copy-pasteable shell text."""
    lines = [f"claude mcp add {name} --scope {scope}"]
    if serpapi:
        lines.append(f"-e {SERPAPI_ENV}={serpapi_value}")
    if maps:
        lines.append(f"-e {MAPS_ENV}={maps_value}")
    lines.append(f"-- {' '.join(launch_argv(binary))}")
    return (" \\\n" + indent).join(lines)


def remove_command(*, scope: str = "user", name: str = SERVER_NAME) -> str:
    """Render the ``claude mcp remove`` command."""
    return f"claude mcp remove {name} --scope {scope}"


# ---------------------------------------------------------------------------
# Key-acquisition steps
# ---------------------------------------------------------------------------


def serpapi_instructions() -> list[str]:
    """Click-path for obtaining a SerpAPI key."""
    return [
        f"Sign up at {SERPAPI_SIGNUP_URL} — no credit card required.",
        "Confirm the email SerpAPI sends you.",
        f"Open {SERPAPI_DASHBOARD_URL} and copy your private API key.",
        f"The free plan gives you {FREE_TIER_SEARCHES} searches/month. "
        "One flight search, one hotel search, one hotel detail lookup, one event search, or one date "
        "sampled by search_cheapest_dates each cost one search.",
    ]


def maps_instructions() -> list[str]:
    """Click-path for obtaining a Google Maps key with the Routes API on."""
    return [
        f"Open {GOOGLE_CONSOLE_URL} and create a project (or pick one).",
        "Enable billing on the project. Google requires a billing account "
        "even for free usage; normal use of this server stays inside the "
        "free monthly credit.",
        f"Enable the Routes API: {ROUTES_API_LIBRARY_URL}. It must be the "
        "Routes API — the older Distance Matrix API is legacy and this "
        "server does not call it.",
        'Go to "APIs & Services" → "Credentials" → "Create credentials" → '
        '"API key", then copy the key.',
        "Optional but recommended: restrict the key to the Routes API so a "
        "leak cannot be used against your other Google services.",
    ]


_KEY_HINTS = {
    SERPAPI_ENV: (
        f"get a free key at {SERPAPI_SIGNUP_URL} "
        f"({FREE_TIER_SEARCHES} searches/month)"
    ),
    MAPS_ENV: (
        f"create one at {GOOGLE_CONSOLE_URL} with the Routes API enabled"
    ),
}


# ---------------------------------------------------------------------------
# Multi-client configuration snippets
# ---------------------------------------------------------------------------


# Client names, config file paths, and JSON shape conventions.
# Each entry: (config_file_path, top_level_key, extra_notes | None).
# Sources verified 2025-07-31:
#   Claude Desktop: https://docs.anthropic.com/en/docs/claude-desktop/mcp
#   Cursor:         https://docs.cursor.com/context/model-context-protocol
#   Windsurf:       https://docs.codeium.com/windsurf/mcp
#   Cline:          https://docs.cline.bot/mcp/configuring-cline-with-mcp-servers
#   VS Code:        MCP extension — stores servers in ``.vscode/mcp.json``
#                    with a top-level ``servers`` key.
CLIENT_CONFIGS: dict[str, dict[str, str | None]] = {
    "claude-code": {
        "config_path": None,  # managed by `claude mcp add` / `claude mcp remove`
        "top_level_key": None,
        "notes": "Use `cosmo-travel-mcp setup --register` — Claude Code manages its own config.",
    },
    "claude-desktop": {
        "config_path": (
            "~/Library/Application Support/Claude/claude_desktop_config.json"
            " (macOS) or %APPDATA%\\Claude\\claude_desktop_config.json (Windows)"
        ),
        "top_level_key": "mcpServers",
        "notes": None,
    },
    "cursor": {
        "config_path": "~/.cursor/mcp.json (global) or .cursor/mcp.json (per-project)",
        "top_level_key": "mcpServers",
        "notes": None,
    },
    "windsurf": {
        "config_path": "~/.codeium/windsurf/mcp_config.json",
        "top_level_key": "mcpServers",
        "notes": None,
    },
    "cline": {
        "config_path": (
            "Cline global MCP settings file. Consult "
            "https://docs.cline.bot/mcp/configuring-cline-with-mcp-servers "
            "for your platform's exact path."
        ),
        "top_level_key": "mcpServers",
        "notes": (
            "Cline stores MCP servers in a JSON settings file rather than "
            "a VS Code workspace file. The path varies by OS; use the docs link "
            "above."
        ),
    },
    "vscode": {
        "config_path": ".vscode/mcp.json (per-project; create it if absent)",
        "top_level_key": "servers",
        "notes": (
            "VS Code's MCP extension uses a top-level ``servers`` key, not "
            "``mcpServers``. If your VS Code extension expects a different key, "
            "consult its documentation."
        ),
    },
}


def render_client_config(
    *,
    client: str,
    binary: str = PACKAGE_NAME,
    serpapi_key: str | None = None,
    maps_key: str | None = None,
    name: str = SERVER_NAME,
) -> str:
    """Render a ready-to-paste config snippet for *client*.

    Keys are never printed as real values — placeholders are used instead.
    """
    if client not in CLIENT_CONFIGS:
        valid = ", ".join(sorted(CLIENT_CONFIGS))
        return f"Unknown client {client!r}. Valid: {valid}"

    info = CLIENT_CONFIGS[client]
    config_path = info["config_path"]
    top_key = info["top_level_key"]
    notes = info.get("notes")

    if top_key is None:
        # claude-code: show the claude mcp add command
        return register_command(
            serpapi=serpapi_key is not None or True,
            maps=maps_key is not None or True,
            serpapi_value=SERPAPI_PLACEHOLDER,
            maps_value=MAPS_PLACEHOLDER,
            binary=binary,
        )

    # Always use placeholders — never print real key values in plain output.
    env_entries: list[str] = [
        f'      "{SERPAPI_ENV}": "{SERPAPI_PLACEHOLDER}"',
        f'      "{MAPS_ENV}": "{MAPS_PLACEHOLDER}"',
    ]
    env_block = ",\n".join(env_entries)

    snippet = (
        f"Add this to {config_path}:\n\n"
        f"{{\n"
        f'  "{top_key}": {{\n'
        f'    "{name}": {{\n'
        f'      "command": "{binary}",\n'
        f'      "args": [],\n'
        f'      "env": {{\n'
        f"{env_block}\n"
        f"      }}\n"
        f"    }}\n"
        f"  }}\n"
        f"}}"
    )

    if notes:
        snippet += f"\n\nNote: {notes}"

    return snippet


def missing_key_message(env_name: str) -> str:
    """Error text for a tool invoked without its API key.

    A model that calls a tool cold — without calling ``check_setup`` first —
    sees only this string, so it has to carry the whole remediation. It states
    the one thing a caller cannot work out on its own: that ``export`` in a
    shell does nothing, because an MCP server receives its environment when it
    is *registered*. The previous wording told users to export the variable,
    which sends them off to fix a problem that stays broken.
    """
    hint = _KEY_HINTS.get(env_name, "obtain the key")
    return (
        f"{env_name} is not set, so this tool cannot run. To fix it: {hint}, "
        f"then run `{PACKAGE_NAME} setup --register` in a terminal. Call "
        "`check_setup` for the full walk-through including the manual "
        "commands. Note: exporting the variable in a shell has no effect — an "
        "MCP server receives its environment at registration time, so a key "
        "added later requires re-registering the server."
    )


def setup_guide(*, need_serpapi: bool, need_maps: bool) -> str:
    """Build the walk-through shown when a key is missing.

    Returned once at the top level rather than repeated on every affected tool:
    the same 40-word remediation echoed five times is noise, and the caller of
    this tool is often an LLM relaying it to a human. "See the README" is a dead
    end inside a chat client, so give the actual commands.
    """
    steps: list[str] = []

    if need_serpapi:
        steps.append(
            f"1. Get a free SerpAPI key — sign up at {SERPAPI_SIGNUP_URL} "
            f"(free tier: {FREE_TIER_SEARCHES} searches/month), then copy the "
            "key from your dashboard. This unlocks flights, multi-city, "
            "accommodations, events, and cheapest-dates."
        )
    if need_maps:
        steps.append(
            f"{len(steps) + 1}. Get a Google Maps key — create one at "
            f"{GOOGLE_CONSOLE_URL} with the **Routes API** enabled (not the "
            "legacy Distance Matrix API). Billing must be enabled on the "
            "project, though the free monthly credit covers normal use. This "
            "unlocks drive-or-fly comparison."
        )

    add_cmd = register_command(
        serpapi=need_serpapi, maps=need_maps, indent="    "
    )
    steps.append(
        f"{len(steps) + 1}. Give the key(s) to this server. The fastest way is "
        "to run these two commands in a terminal — the second prompts for the "
        "keys, checks them against the real APIs, and registers the server:\n"
        f"  {install_command()}\n"
        "  # (until the first PyPI release, use: "
        f"{install_from_source_command()})\n"
        f"  {PACKAGE_NAME} setup --register\n"
        "To do it by hand instead — if it is NOT yet registered with Claude "
        "Code (use the absolute path from `which cosmo-travel-mcp`, since the "
        "client may not inherit your PATH):\n"
        f"  {add_cmd}\n"
        "If it IS already registered (which it is, since you are calling this "
        "tool), re-register it so the keys are attached:\n"
        f"  {remove_command()}\n"
        "  …then run the add command above."
    )
    steps.append(
        f"{len(steps) + 1}. Restart the MCP client, then call check_setup "
        "again to confirm."
    )
    steps.append(
        "For clients other than Claude Code, run "
        f"`{PACKAGE_NAME} setup --client <name>` to see a ready-to-paste "
        "JSON config block for your client."
    )

    return "\n\n".join(steps)
