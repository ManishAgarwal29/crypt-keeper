"""The Kiro configuration in this repository is valid, points at things that exist, and actually fires.

These tests exist because of a real mistake: the conditional steering file used a regular expression
where Kiro expects a glob. The file was valid YAML and it silently never loaded. So the checks here are
about behaviour (does this pattern match the files it is meant for?), not only about shape.

Rules come from the Kiro docs on custom agents, skills, steering and hooks, and from the Agent Plugins
manifest schema. No network access is used.
"""
import json
import re
from pathlib import Path

import pytest
import yaml

from coroner import autopsy, deprecations
from necromancer import rewriter

ROOT = Path(__file__).resolve().parents[2]
AGENT_FILES = sorted((ROOT / ".kiro" / "agents").glob("*.md"))
SKILL_FILES = sorted([*(ROOT / ".kiro" / "skills").glob("*/SKILL.md"), *(ROOT / "skills").glob("*/SKILL.md")])
STEERING_FILES = sorted((ROOT / ".kiro" / "steering").glob("*.md"))
HOOK_FILES = sorted((ROOT / ".kiro" / "hooks").glob("*.json"))

AGENT_KEYS = {
    "name", "description", "prompt", "model", "tools", "excludedTools", "permissions", "resources",
    "hooks", "mcpServers", "includeMcpJson", "includePowers", "welcomeMessage",
}
CAPABILITIES = {
    "fs_read", "fs_write", "shell", "web_fetch", "web_search", "mcp", "subagent", "skill", "power",
    "context", "diagnostics", "sandbox_network", "all", "builtin", "filesystem",
}
EFFECTS = {"allow", "ask", "deny"}
TOOL_SELECTOR = re.compile(r"^(\*|@builtin|@mcp|@[\w.-]+(/[\w.-]+)?|[a-z][a-z_]*)$")
PLUGIN_KEYS = {"$schema", "name", "version", "description", "author", "homepage", "repository", "license", "keywords", "extensions"}
PLUGIN_NAME = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SKILL_NAME = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

MAX_RESOURCE_FILES, MAX_RESOURCE_CHARS, MAX_FILE_CHARS = 32, 100_000, 50_000


def split_front_matter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return {}, text
    head, sep, body = text[4:].partition("\n---\n")
    assert sep, f"{path.name}: front matter is never closed"
    data = yaml.safe_load(head) or {}
    assert isinstance(data, dict), f"{path.name}: front matter must be a mapping"
    return data, body


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def expand(pattern: str) -> list[Path]:
    """Files a resource URI points at (relative to the repository root)."""
    pattern = pattern.split("://", 1)[1].removeprefix("./")
    if any(ch in pattern for ch in "*?["):
        return sorted(p for p in ROOT.glob(pattern) if p.is_file())
    return [ROOT / pattern] if (ROOT / pattern).is_file() else []


def agent(name: str) -> dict:
    return split_front_matter(ROOT / ".kiro" / "agents" / f"{name}.md")[0]


def deny_patterns(config: dict, capability: str) -> list[str]:
    rules = config.get("permissions", {}).get("rules", [])
    return [m for r in rules if r["capability"] == capability and r["effect"] == "deny" for m in r["match"]]


def allow_patterns(config: dict, capability: str) -> list[str]:
    rules = config.get("permissions", {}).get("rules", [])
    return [m for r in rules if r["capability"] == capability and r["effect"] == "allow" for m in r["match"]]


# --- agents -----------------------------------------------------------------------------------------


def test_the_expected_agents_exist():
    assert {p.stem for p in AGENT_FILES} == {"sandbox-auditor", "necromancer", "grave-digger"}


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_agent_front_matter_is_well_formed(path):
    config, prompt = split_front_matter(path)
    assert config["name"] == path.stem
    assert len(config["description"]) > 40
    assert set(config) <= AGENT_KEYS, f"unknown keys: {set(config) - AGENT_KEYS}"
    assert len(prompt.strip()) > 300, "the body is the system prompt and must say something"
    for tool in config.get("tools", []):
        assert TOOL_SELECTOR.match(tool), f"odd tool selector {tool!r}"
    for rule in config.get("permissions", {}).get("rules", []):
        assert rule["capability"] in CAPABILITIES and rule["effect"] in EFFECTS
        assert rule["match"] and all(isinstance(m, str) and m for m in rule["match"])


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_agent_resources_exist_and_fit_kiros_limits(path):
    config, _ = split_front_matter(path)
    files: list[Path] = []
    for uri in config.get("resources", []):
        if uri.startswith("file://"):
            matches = expand(uri)
            assert matches, f"{uri} matches nothing"
            files += matches
        else:
            assert uri.startswith("skill://"), f"unsupported resource {uri!r}"
            matches = expand(uri)
            assert matches, f"{uri} matches nothing"
            data, _ = split_front_matter(matches[0])
            assert data["name"] == matches[0].parent.name
    chars = [len(p.read_text(encoding="utf-8")) for p in files]
    assert len(files) <= MAX_RESOURCE_FILES
    assert max(chars, default=0) <= MAX_FILE_CHARS, "Kiro truncates a file past 50,000 characters"
    assert sum(chars) <= MAX_RESOURCE_CHARS, f"{sum(chars)} characters; Kiro stops loading past 100,000"


@pytest.mark.parametrize("path", AGENT_FILES, ids=lambda p: p.stem)
def test_agent_mcp_servers_point_at_real_scripts(path):
    config, _ = split_front_matter(path)
    for name, server in config.get("mcpServers", {}).items():
        scripts = [a for a in server.get("args", []) if a.endswith(".py")]
        assert scripts, f"{name} runs no script"
        assert all((ROOT / s).is_file() for s in scripts)


@pytest.mark.parametrize("name", ["sandbox-auditor", "grave-digger"])
def test_read_only_agents_cannot_write_or_run_commands(name):
    config = agent(name)
    assert not {"write", "shell"} & set(config["tools"])
    assert deny_patterns(config, "fs_write") == ["**"], "defence in depth: deny writes even if a tool slips in"
    assert deny_patterns(config, "shell") == ["*"]


def test_the_auditor_loads_the_steering_it_audits_against():
    """Steering is not included automatically for custom agents; the docs say to list it as a resource."""
    assert "file://.kiro/steering/sandbox-safety.md" in agent("sandbox-auditor")["resources"]


def test_the_triage_agent_carries_its_own_mcp_server_and_may_only_use_it():
    config = agent("grave-digger")
    assert config["mcpServers"]["crypt-keeper"]["args"] == ["mcp-server/server.py"]
    assert "@crypt-keeper" in config["tools"] and config["includeMcpJson"] is False
    assert allow_patterns(config, "mcp") == ["crypt-keeper/*"]


def test_the_necromancer_agent_is_fenced_in():
    config = agent("necromancer")
    writable, blocked = allow_patterns(config, "fs_write"), deny_patterns(config, "fs_write")
    assert {"backend/coroner/**", "backend/necromancer/**", "backend/tests/**"} <= set(writable)
    for protected in ("backend/api/**", "backend/proof/**", ".kiro/steering/**", ".kiro/hooks/**", "frontend/**"):
        assert protected in blocked
    roots = lambda patterns: [p.removesuffix("/**") for p in patterns]  # noqa: E731
    for w in roots(writable):
        for b in roots(blocked):
            assert not (w.startswith(b) or b.startswith(w)), f"allow {w!r} overlaps deny {b!r}"
    assert {"git push*", "git reset*"} <= set(deny_patterns(config, "shell"))


# --- skills -----------------------------------------------------------------------------------------


def test_both_skills_exist():
    assert {p.parent.name for p in SKILL_FILES} == {"add-removed-api", "triage-abandoned-package"}


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: p.parent.name)
def test_skill_front_matter_follows_the_skill_format(path):
    data, body = split_front_matter(path)
    assert data["name"] == path.parent.name, "the name must match the folder"
    assert SKILL_NAME.match(data["name"]) and len(data["name"]) <= 64
    assert 0 < len(data["description"]) <= 1024
    assert set(data) <= {"name", "description"}
    assert len(body.strip()) > 400


def test_the_add_removed_api_skill_names_things_that_exist():
    _, body = split_front_matter(ROOT / ".kiro" / "skills" / "add-removed-api" / "SKILL.md")
    for symbol in ("REMOVED_MODULES", "REMOVED_ATTRS", "REMOVED_TESTCASE_METHODS", "_py2", "Removal", "scan_source"):
        assert hasattr(deprecations, symbol) and symbol in body
    for symbol in ("RENAMES", "BUILTIN_REPLACEMENTS", "TESTCASE_RENAMES", "ABC_MOVES", "rewrite_deprecated"):
        assert hasattr(rewriter, symbol) and symbol in body
    tests = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "backend" / "tests").glob("test_*.py"))
    defined = set(re.findall(r"def (test_\w+)", tests))
    for name in re.findall(r"`(test_\w+\*?)`", body):
        if name.endswith("*"):
            assert any(d.startswith(name[:-1]) for d in defined), f"no test starts with {name}"
        else:
            assert name in defined, f"the skill mentions {name}, which does not exist"


def test_the_triage_skill_states_the_thresholds_the_coroner_really_uses():
    """If someone changes the Coroner's rules, this fails until the skill says the same thing."""
    _, body = split_front_matter(ROOT / "skills" / "triage-abandoned-package" / "SKILL.md")
    assert autopsy.DORMANT_AFTER_DAYS == 365 and "under 1 year alive" in body
    assert autopsy.DEAD_AFTER_DAYS == 365 * 3 and "3 years or more dead" in body
    assert autopsy.PYTHON_LAG_MINORS == 3 and "3 or more minors" in body


# --- steering ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", STEERING_FILES, ids=lambda p: p.stem)
def test_steering_inclusion_modes_are_valid(path):
    data, _ = split_front_matter(path)
    assert data.get("inclusion", "always") in {"always", "fileMatch", "manual", "auto"}
    if data.get("inclusion") == "fileMatch":
        patterns = data["fileMatchPattern"]
        patterns = [patterns] if isinstance(patterns, str) else patterns
        assert patterns and all(isinstance(p, str) for p in patterns)
        for pattern in patterns:
            assert not re.search(r"[()|\\^$+]", pattern), f"{pattern!r} is a regex; Kiro wants a glob"


def steering_matches(file_rel: str) -> bool:
    data, _ = split_front_matter(ROOT / ".kiro" / "steering" / "sandbox-safety.md")
    patterns = data["fileMatchPattern"]
    patterns = [patterns] if isinstance(patterns, str) else patterns
    return any(
        Path(ROOT / file_rel).match(pattern) if "**" not in pattern else False for pattern in patterns
    ) or any(file_rel in [rel(p) for p in ROOT.glob(pattern)] for pattern in patterns)


@pytest.mark.parametrize("file_rel", [
    "backend/api/sandbox.py", "backend/api/main.py", "backend/api/guards.py", "backend/api/limits.py",
    "backend/api/exhumer.py", "backend/proof/policy.py", "backend/proof/discovery.py", "backend/proof/verdict.py",
])
def test_the_safety_steering_loads_for_the_files_it_guards(file_rel):
    assert (ROOT / file_rel).is_file()
    assert steering_matches(file_rel), f"sandbox-safety.md would not load while editing {file_rel}"


@pytest.mark.parametrize("file_rel", ["backend/necromancer/revive.py", "backend/coroner/autopsy.py", "frontend/app.js"])
def test_the_safety_steering_stays_out_of_unrelated_work(file_rel):
    assert not steering_matches(file_rel)


# --- hooks ------------------------------------------------------------------------------------------


def hooks() -> list[dict]:
    return [h for path in HOOK_FILES for h in json.loads(path.read_text(encoding="utf-8"))["hooks"]]


def fires_on(hook: dict, file_rel: str) -> bool:
    """Hook matchers are regular expressions; Windows and POSIX separators are both tried."""
    return any(re.search(hook["matcher"], candidate) for candidate in (file_rel, file_rel.replace("/", "\\")))


def test_every_hook_is_complete():
    assert len(hooks()) == 3
    for hook in hooks():
        assert hook["name"] and hook["trigger"] in {"PostFileSave", "PostTaskExec", "PreToolUse", "PostToolUse", "SessionStart"}
        action = hook["action"]
        assert action["type"] in {"command", "agent"}
        assert (action.get("command") or action.get("prompt", "")).strip()


def test_file_save_hooks_match_real_files():
    save_hooks = [h for h in hooks() if h["trigger"] == "PostFileSave"]
    assert len(save_hooks) == 2
    for hook in save_hooks:
        re.compile(hook["matcher"])
        assert any(fires_on(hook, rel(p)) for p in (ROOT / "backend").rglob("*.py")), f"{hook['name']} matches nothing"


def test_the_safety_hook_fires_only_for_the_risky_files():
    safety = next(h for h in hooks() if h["name"].startswith("Safety review"))
    for risky in ("backend/api/sandbox.py", "backend/api/main.py", "backend/api/guards.py", "backend/api/limits.py",
                  "backend/api/exhumer.py", "backend/proof/policy.py"):
        assert fires_on(safety, risky)
    for other in ("backend/necromancer/revive.py", "backend/coroner/autopsy.py", "backend/tests/test_api.py"):
        assert not fires_on(safety, other)


def test_the_quick_test_hook_covers_all_backend_python():
    quick = next(h for h in hooks() if h["name"].startswith("Quick tests"))
    assert fires_on(quick, "backend/necromancer/revive.py") and not fires_on(quick, "frontend/app.js")


# --- the power --------------------------------------------------------------------------------------


def test_plugin_manifest_follows_the_agent_plugins_schema():
    manifest = json.loads((ROOT / "plugin.json").read_text(encoding="utf-8"))
    assert set(manifest) <= PLUGIN_KEYS, f"unknown keys: {set(manifest) - PLUGIN_KEYS}"
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert PLUGIN_NAME.match(manifest["name"]) and len(manifest["name"]) <= 64
    assert set(manifest["author"]) <= {"name", "email", "url"} and manifest["author"]["name"]
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["keywords"] and all(isinstance(k, str) and k for k in manifest["keywords"])
    assert "MIT" in (ROOT / "LICENSE").read_text(encoding="utf-8").splitlines()[0] and manifest["license"] == "MIT"


MCP_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
STDIO_SERVER_KEYS = {"type", "command", "args", "env", "cwd"}
PLUGIN_CWD = re.compile(r"^(?:\./|\$\{PLUGIN_ROOT\}(?:/|$)|\$\{PLUGIN_DATA\}(?:/|$))")


def power_server() -> dict:
    return json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))["mcpServers"]["crypt-keeper"]


def test_the_power_ships_a_skill():
    assert (ROOT / "skills" / "triage-abandoned-package" / "SKILL.md").is_file()


def test_mcp_json_follows_the_agent_plugins_schema():
    """Rules from https://agent-plugins.org/schemas/1.0.0/mcp.schema.json. An earlier version of this file
    lacked `$schema` and `type` and an older, looser test still passed it."""
    config = json.loads((ROOT / "mcp.json").read_text(encoding="utf-8"))
    assert set(config) == {"$schema", "mcpServers"}
    assert config["$schema"] == MCP_SCHEMA_URL
    assert set(config["mcpServers"]) == {"crypt-keeper"}
    server = power_server()
    assert set(server) <= STDIO_SERVER_KEYS, f"unknown keys: {set(server) - STDIO_SERVER_KEYS}"
    assert server["type"] == "stdio"
    assert isinstance(server["command"], str) and server["command"]
    assert all(isinstance(a, str) for a in server.get("args", []))
    env = server.get("env", {})
    assert all(isinstance(v, str) for v in env.values())
    assert not {"PLUGIN_ROOT", "PLUGIN_DATA"} & set(env), "those names are reserved"


def test_the_power_does_not_depend_on_the_workspace_it_is_opened_in():
    """The script path is relative, so the working directory must be pinned to the plugin root."""
    server = power_server()
    assert any(a.endswith(".py") and not Path(a).is_absolute() for a in server["args"])
    assert PLUGIN_CWD.match(server["cwd"]) and server["cwd"].startswith(("${PLUGIN_ROOT}", "./"))
    assert all((ROOT / a).is_file() for a in server["args"] if a.endswith(".py"))


def test_the_power_server_starts_exactly_as_its_manifest_says():
    """Launch it the way Kiro would, with the working directory set to the plugin root (here: the repo root)."""
    pytest.importorskip("mcp")
    import asyncio
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    server = power_server()

    async def talk() -> list[str]:
        params = StdioServerParameters(command=sys.executable, args=server["args"], cwd=ROOT)
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return sorted(t.name for t in (await session.list_tools()).tools)

    assert asyncio.run(asyncio.wait_for(talk(), timeout=60)) == ["get_pypi_metadata", "get_repo_tree"]
