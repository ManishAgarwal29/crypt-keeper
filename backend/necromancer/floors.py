"""Minimum versions of compiled packages that publish wheels for each Python 3.x.

Derived from PyPI release files: for each package and Python minor version, the floor is
the earliest stable, non-yanked release that ships at least 4 `cp3XX-cp3XX` wheels.
Requiring 4 wheels skips early releases that uploaded a stray wheel for a Python still in
alpha. A floor is a minimum that has real wheel coverage, not a guarantee the package
works with the project's code.

Only packages whose wheels are tied to one CPython version (not abi3) appear here.
Pure data, no I/O.
"""
from __future__ import annotations

PYTHON_FLOORS: dict[str, dict[int, str]] = {
    "numpy": {9: "1.19.3", 10: "1.21.3", 11: "1.23.2", 12: "1.26.0", 13: "2.1.0"},
    "scipy": {9: "1.5.4", 10: "1.7.2", 11: "1.9.2", 12: "1.11.2", 13: "1.14.1"},
    "pandas": {9: "1.1.3", 10: "1.3.4", 11: "1.5.0", 12: "2.1.1", 13: "2.2.3"},
    "pillow": {9: "8.0.0", 10: "8.3.2", 11: "9.2.0", 12: "10.0.0", 13: "10.4.0"},
    "matplotlib": {9: "3.3.3", 10: "3.5.0", 11: "3.6.0", 12: "3.7.3", 13: "3.9.2"},
    "scikit-learn": {9: "0.24.0", 10: "1.0.2", 11: "1.1.3", 12: "1.3.1", 13: "1.5.2"},
    "lxml": {9: "4.6.0", 10: "4.6.4", 11: "4.9.2", 12: "4.9.3", 13: "5.3.0"},
    "pyyaml": {9: "5.4", 10: "6.0", 11: "6.0", 12: "6.0.1", 13: "6.0.2"},
    "cffi": {9: "1.14.3", 10: "1.15.0", 11: "1.15.1", 12: "1.16.0", 13: "1.17.0"},
    "greenlet": {9: "1.0.0", 10: "1.1.0", 11: "1.1.3", 12: "3.0.0", 13: "3.1.0"},
    "gevent": {9: "21.1.2", 10: "21.12.0", 11: "22.10.1", 12: "23.7.0", 13: "24.10.1"},
    "psycopg2-binary": {9: "2.8.6", 10: "2.9.1", 11: "2.9.5", 12: "2.9.9", 13: "2.9.10"},
    "markupsafe": {9: "1.1.1", 10: "2.0.1", 11: "2.1.2", 12: "2.1.3", 13: "3.0.0"},
    "grpcio": {9: "1.33.1", 10: "1.41.0", 11: "1.49.1", 12: "1.59.0", 13: "1.66.2"},
    "msgpack": {9: "1.0.1", 10: "1.0.3", 11: "1.0.5", 12: "1.0.6", 13: "1.1.0"},
    "regex": {9: "2020.9.27", 10: "2021.8.21", 11: "2022.9.11", 12: "2023.10.3", 13: "2024.9.11"},
    "ujson": {9: "4.0.0", 10: "4.2.0", 11: "5.5.0", 12: "5.8.0", 13: "5.10.0"},
    "aiohttp": {9: "3.7.0", 10: "3.8.0", 11: "3.8.3", 12: "3.9.0", 13: "3.10.5"},
    "multidict": {9: "5.0.0", 10: "5.2.0", 11: "6.0.3", 12: "6.0.5", 13: "6.1.0"},
    "yarl": {9: "1.6.1", 10: "1.7.0", 11: "1.8.2", 12: "1.9.3", 13: "1.9.7"},
    "frozenlist": {9: "1.1.0", 10: "1.2.0", 11: "1.3.3", 12: "1.4.1", 13: "1.5.0"},
}


def floor_for(canonical_name: str, target_python: str) -> str | None:
    """Floor version for `canonical_name` on `target_python`, or None if unknown.

    A target newer than the table uses the newest known floor, which is still a valid
    (weaker) minimum. A target older than the table has no floor.
    """
    by_minor = PYTHON_FLOORS.get(canonical_name)
    if not by_minor:
        return None
    try:
        major, minor, *_ = target_python.strip().split(".")
        if int(major) != 3:
            return None
        usable = [m for m in by_minor if m <= int(minor)]
    except ValueError:
        return None
    return by_minor[max(usable)] if usable else None
