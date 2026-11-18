"""Which packages a public deployment may execute. Pure.

Running a package's code is only safe to offer to strangers for packages we chose ourselves.
`CRYPT_KEEPER_PROOF_ALLOWLIST` is a comma-separated list of PyPI names (or GitHub URLs). When it is
unset or empty there is no restriction, which is the right default for local use.
"""
from __future__ import annotations

import re

from packaging.utils import canonicalize_name

CURATED_REASON = (
    "Proof of life on this server is limited to a curated list of packages. "
    "Run Crypt Keeper locally to check any package."
)


def normalize(identifier: str) -> str:
    """One comparable key per package: canonical PyPI name, or `github.com/owner/repo` for a URL."""
    text = identifier.strip()
    if "/" in text or text.lower().startswith(("http:", "https:")):
        text = re.sub(r"^https?://(www\.)?", "", text.lower())
        return re.sub(r"(\.git)?/*$", "", text)
    return canonicalize_name(text)


def parse_allowlist(text: str | None) -> frozenset[str] | None:
    """The set of allowed keys, or None when no restriction is configured."""
    items = [item.strip() for item in (text or "").split(",") if item.strip()]
    return frozenset(normalize(item) for item in items) if items else None


def is_allowed(identifier: str, allowlist: frozenset[str] | None) -> bool:
    return allowlist is None or normalize(identifier) in allowlist
