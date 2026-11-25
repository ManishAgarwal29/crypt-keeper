"""Turn a revival into files a person can use: a patch, the revived sources and a plain report.

Pure: no network, no disk, nothing is executed. The zip is built in memory with fixed timestamps, so the
same revival always gives the same bytes.

What is in the zip, and what is not:
  * `patch.diff`: every rewrite as a patch that `git apply` / `patch -p1` understand, relative to the
    package root as the exhumer saw it (an sdist's top-level directory is already stripped). The patch
    is UTF-8 text. A file that declares another encoding (`# coding: latin-1`) is written to `revived/` in
    that encoding, but its patch lines are UTF-8, so a patch touching non-ASCII lines of such a file may not
    apply byte for byte. Use the file in `revived/` for those.
  * `revived/`: the revived `.py` files. Only Python files are exhumed, so this is **not** a complete
    package (no data files, no README). Apply the patch to your own checkout for that.
  * `requirements.revived.txt`: the dependency constraints after revival.
  * `REVIVAL_REPORT.md`: what was found, what was changed, what still needs a person.
"""
from __future__ import annotations

import io
import re
import zipfile

from models import AutopsyReport, PackageSnapshot, RevivalResult
from proof.discovery import encode_source, safe_relative_path

_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_NO_EOL = "\\ No newline at end of file\n"


def split_lines(text: str) -> list[str]:
    """Lines split on `\\n` only, each keeping its ending, as `git` counts them.

    `str.splitlines` also splits on form feeds and a few Unicode separators, which old Python files
    contain; a patch built that way would not apply.
    """
    if not text:
        return []
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def file_patch(path: str, before: str, after: str) -> str:
    """One file's changes as a git-style unified diff, with `\\ No newline at end of file` markers."""
    import difflib

    out: list[str] = []
    for line in difflib.unified_diff(split_lines(before), split_lines(after), f"a/{path}", f"b/{path}", lineterm="\n"):
        out.append(line if line.endswith("\n") else line + "\n" + _NO_EOL)
    if not out:
        return ""
    return f"diff --git a/{path} b/{path}\n" + "".join(out)


def build_patch(revival: RevivalResult) -> str:
    """All rewrites in path order. Files whose path is not a safe relative path are left out."""
    return "".join(
        file_patch(d.path, d.before, d.after) for d in sorted(revival.diffs, key=lambda d: d.path)
        if safe_relative_path(d.path) is not None
    )


def archive_name(identifier: str) -> str:
    """A file name for the download that cannot carry a path, a quote or a header break."""
    name = identifier.strip().rstrip("/").rsplit("/", 1)[-1]
    name = re.sub(r"\.git$", "", name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "package"
    return f"crypt-keeper-{name[:60]}-revived.zip"


def build_report(snapshot: PackageSnapshot, report: AutopsyReport, revival: RevivalResult) -> str:
    changed = [(b, a) for b, a in zip(snapshot.dependencies, revival.dependencies) if b != a]
    lines = [
        f"# Revival report: {snapshot.ref.identifier}",
        "",
        f"- Target Python: {report.target_python}",
        f"- Diagnosis: {report.liveness.value}, {len(report.findings)} findings",
        f"- Files rewritten: {len(revival.diffs)}",
        f"- Last release: {snapshot.last_release_date or 'unknown'}",
        "",
        "This revival is static: nothing was run to produce it. Run the package's tests on the target",
        "Python before relying on it. Crypt Keeper's proof of life is the check that does that.",
        "",
    ]
    if snapshot.truncated:
        lines += ["**The exhumed source was incomplete** (a size or file-count limit, or a file that could not",
                  "be read), so some files may be missing from this revival.", ""]
    lines += ["## What was changed", ""]
    lines += [f"- {item}" for item in revival.applied] or ["- Nothing could be rewritten automatically."]
    if changed:
        lines += ["", "## Dependencies", ""] + [f"- `{b}` -> `{a}`" for b, a in changed]
    lines += ["", "## Still needs a person", ""]
    lines += [f"- {item}" for item in revival.manual_items] or ["- Nothing was left for a person."]
    lines += ["", "## Contents of this archive", "",
              "- `patch.diff`: apply with `git apply patch.diff` from the package root.",
              "- `revived/`: the revived `.py` files only. This is not a complete package.",
              "- `requirements.revived.txt`: dependency constraints after revival.", ""]
    return "\n".join(lines)


def build_export(snapshot: PackageSnapshot, report: AutopsyReport, revival: RevivalResult) -> bytes:
    """The zip described in the module docstring."""
    revived = dict(snapshot.source_map)
    for d in revival.diffs:
        revived[d.path] = d.after
    deps = list(revival.dependencies) or list(snapshot.dependencies)

    members: dict[str, bytes] = {
        "REVIVAL_REPORT.md": build_report(snapshot, report, revival).encode("utf-8"),
        "patch.diff": build_patch(revival).encode("utf-8"),
        "requirements.revived.txt": "".join(d + "\n" for d in deps).encode("utf-8"),
    }
    for path in sorted(revived):
        if safe_relative_path(path) is not None:
            members[f"revived/{path}"] = encode_source(revived[path])  # in the file's own declared encoding

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            info = zipfile.ZipInfo(name, _ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, content)
    return buf.getvalue()
