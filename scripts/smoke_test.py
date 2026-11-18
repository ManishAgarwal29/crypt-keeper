"""Smoke-test a running Crypt Keeper (local or deployed). Standard library only.

    python scripts/smoke_test.py http://127.0.0.1:8080
    python scripts/smoke_test.py https://crypt-keeper-xxxx.a.run.app --package attrdict

Checks, in order: /health, /proof-of-life/status, a full streamed run of --package (default
attrdict) and, when the server has a curated list, that an unlisted package is NOT executed.
It sends the same Origin header a browser on that site would. Exit code 0 means every check passed.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

UNLISTED = "docopt"  # a real, tiny package that is not on the demo list


def call(base: str, path: str, body: dict | None = None, timeout: float = 600):
    headers = {"Content-Type": "application/json", "Origin": f"{urlsplit(base).scheme}://{urlsplit(base).netloc}"}
    data = json.dumps(body).encode() if body is not None else None
    return urllib.request.urlopen(urllib.request.Request(base + path, data=data, headers=headers), timeout=timeout)


def stream(base: str, package: str, target: str) -> list[dict]:
    body = {"ref": {"source": "pypi", "identifier": package}, "target_python": target}
    events = []
    started = time.time()
    with call(base, "/revive/stream", body) as response:
        for raw in response:
            if not raw.strip():
                continue
            event = json.loads(raw)
            events.append(event)
            if event["event"] == "stage":
                print(f"   {time.time() - started:6.1f}s  {event['stage']:<13} {event['status']:<8} {event.get('detail', '')}")
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base_url")
    parser.add_argument("--package", default="attrdict")
    parser.add_argument("--target", default="3.12")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    try:
        health = json.load(call(base, "/health"))
        check("health", health.get("status") == "the crypt is open", str(health))
        status = json.load(call(base, f"/proof-of-life/status?target_python={args.target}"))
        print(f"   status: {status}")
        check("proof of life can run", status["enabled"], str(status))
        check(f"Python {args.target} is installed", status["target_python_matched"], f"ran on {status['python_used']}")

        print(f"\nStreaming a full run of {args.package}:")
        events = stream(base, args.package, args.target)
        result = next((e for e in events if e["event"] == "result"), None)
        error = next((e for e in events if e["event"] == "error"), None)
        check("run finished with a result", result is not None, json.dumps(error))
        if result:
            data = result["data"]
            print(f"   verdict: {data['verdict']} - {data['verdict_reason']}")
            check("progress arrived as separate stage events", sum(e["event"] == "stage" for e in events) >= 6)
            check("verdict is resurrected, improved or already_alive", data["verdict"] in ("resurrected", "improved", "already_alive"), data["verdict"])

        if status.get("limited_to") is not None:
            print(f"\nCurated list is active ({', '.join(status['limited_to'])}). Checking {UNLISTED} is NOT executed:")
            other = stream(base, UNLISTED, args.target)
            final = next((e for e in other if e["event"] == "result"), None)
            check("unlisted package returns a result", final is not None)
            if final:
                check("unlisted package got no verdict", final["data"]["verdict"] == "unverified", final["data"]["verdict"])
                check("unlisted package still got an autopsy", bool(final["data"]["autopsy"]["liveness"]))
            check("no proof stage ran for the unlisted package",
                  not any(e.get("stage", "").startswith("proof") and e.get("status") in ("start", "done") for e in other))
            try:
                call(base, "/proof-of-life", {"snapshot": {"ref": {"source": "pypi", "identifier": "x"}}})
                check("posting your own source is refused", False, "request succeeded")
            except urllib.error.HTTPError as err:
                check("posting your own source is refused", err.code == 403, str(err.code))
    except (urllib.error.URLError, OSError, ValueError) as exc:
        check("server reachable", False, f"{type(exc).__name__}: {exc}")

    print("\n" + ("All checks passed." if not failures else f"{len(failures)} check(s) failed: {failures}"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
