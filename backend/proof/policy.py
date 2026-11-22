"""Which packages a server may execute. Pure.

Running a package's code is only safe to offer to strangers for packages we chose ourselves.
Two environment variables decide it:

* `CRYPT_KEEPER_PROOF_ALLOWLIST`: comma-separated PyPI names (or GitHub URLs) that may be executed.
  The single value `*` means "any package" and has to be written out on purpose.
* `CRYPT_KEEPER_PUBLIC`: set to `1` in the public image. It changes what a *missing* list means.

The rule that matters is that every ambiguous case fails closed:

| `PROOF_ALLOWLIST`      | local run (`PUBLIC` unset) | public image (`PUBLIC=1`) |
|------------------------|----------------------------|---------------------------|
| not set                | any package                | **no package**            |
| set but blank          | **no package**             | **no package**            |
| `a,b`                  | only a and b               | only a and b              |
| `*`                    | any package                | any package               |

So a deploy mistake (a variable removed, a blank value from a quoting slip) can never switch on
execution for the world. Only a deliberate `*` does that.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

UNRESTRICTED = "*"
APPROVED_FILE = Path(__file__).with_name("approved_dependencies.json")

CURATED_REASON = (
    "Proof of life on this server is limited to a curated list of packages. "
    "Run Crypt Keeper locally to check any package."
)
NOT_ENABLED_REASON = (
    "Proof of life is not enabled for any package on this server. "
    "Run Crypt Keeper locally to check a package."
)

_OFF = {"0", "false", "no", "off"}


def normalize(identifier: str) -> str:
    """One comparable key per package: canonical PyPI name, or `github.com/owner/repo` for a URL."""
    text = identifier.strip()
    if "/" in text or text.lower().startswith(("http:", "https:")):
        text = re.sub(r"^https?://(www\.)?", "", text.lower())
        return re.sub(r"(\.git)?/*$", "", text)
    return canonicalize_name(text)


def is_public(raw: str | None) -> bool:
    """Whether `CRYPT_KEEPER_PUBLIC` asks for public-host behaviour.

    Only a variable that is not set, or is set to an explicit "off" value, means a local run. Anything
    else counts as public, including a blank value or a typo such as `enabled`: a mistake here must
    make the server stricter, never looser.
    """
    return raw is not None and raw.strip().lower() not in _OFF


def resolve_allowlist(raw: str | None, public: bool) -> frozenset[str] | None:
    """The set of keys that may be executed, or None meaning "no restriction".

    `raw` is the variable's value, or None when it is not set. An empty set means nothing may run.
    """
    if raw is None:
        return frozenset() if public else None
    items = [item.strip() for item in raw.split(",") if item.strip()]
    if items == [UNRESTRICTED]:
        return None
    # A "*" mixed in with names is not a valid opt-in; it is dropped and the names still apply.
    return frozenset(normalize(item) for item in items if item != UNRESTRICTED)


def is_allowed(identifier: str, allowlist: frozenset[str] | None) -> bool:
    return allowlist is None or normalize(identifier) in allowlist


def dependency_names(deps: list[str]) -> list[str]:
    """Canonical project names of requirement strings. A string that is not a valid requirement keeps its
    raw text, so it can never match an approved name."""
    names: list[str] = []
    for raw in deps:
        try:
            names.append(canonicalize_name(Requirement(raw).name))
        except InvalidRequirement:
            names.append(raw.strip())
    return names


def unapproved_dependencies(identifier: str, deps: list[str], approved: dict[str, frozenset[str]]) -> list[str] | None:
    """The dependencies of `identifier` that nobody reviewed, or None if the package has no reviewed list.

    Approving a package name is not enough on a public host: the package installs its dependencies, and
    each one runs code on import. `approved` maps a package key to the dependency names a person checked.
    """
    reviewed = approved.get(normalize(identifier))
    if reviewed is None:
        return None
    return sorted({name for name in dependency_names(deps) if name not in reviewed})


def parse_approved(text: str) -> dict[str, frozenset[str]]:
    """Read the reviewed-dependency file: `{"package": {"dependencies": ["name", ...]}}`. Malformed means none."""
    try:
        data = json.loads(text)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    approved: dict[str, frozenset[str]] = {}
    for key, entry in data.items():
        names = entry.get("dependencies") if isinstance(entry, dict) else None
        if isinstance(key, str) and isinstance(names, list) and all(isinstance(n, str) for n in names):
            approved[normalize(key)] = frozenset(canonicalize_name(n) for n in names)
    return approved


def load_approved(path: Path = APPROVED_FILE) -> dict[str, frozenset[str]]:
    try:
        return parse_approved(path.read_text(encoding="utf-8"))
    except OSError:
        return {}


def unapproved_reason(identifier: str, bad: list[str] | None) -> str:
    if bad is None:
        return (
            f"Proof of life was skipped: '{identifier}' has no reviewed dependency list on this server, "
            "so its dependencies cannot be installed. Run Crypt Keeper locally to check it."
        )
    return (
        "Proof of life was skipped: these dependencies have not been reviewed for this server: "
        f"{', '.join(bad)}. Run Crypt Keeper locally to check this package."
    )


def skip_reason(allowlist: frozenset[str] | None) -> str:
    """What to tell the user when proof of life is skipped for a package."""
    return CURATED_REASON if allowlist else NOT_ENABLED_REASON
