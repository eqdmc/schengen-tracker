#!/usr/bin/env python3
"""check-ruff.py — a baseline-gated ruff check (new violations only).

Runs ruff over the whole repository and fails on findings that are not in the
accepted baseline. The baseline is keyed by <path>::<rule> (no line numbers,
so moving code does not churn it), which makes the gate a ratchet: existing
debt is recorded, new debt fails, and --write-baseline is the deliberate way
to accept more (never in a feature PR).

Why whole-repo and not git diff: a shallow checkout can make a diff-based
change set empty, which silently disables lint. A whole-tree scan plus a
committed baseline is hermetic and reproducible.

    check-ruff.py                     # check against config/ruff-baseline.json
    check-ruff.py --write-baseline    # accept the current findings (reviewed)
    check-ruff.py --strict            # no baseline: any finding fails
    check-ruff.py --json              # machine-readable diff

Exit: 0 within baseline, 1 new findings, 2 cannot run (fail closed).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

DEFAULT_RUFF = "0.16.1"
BASELINE_REL = "config/ruff-baseline.json"


def repo_root(start: Path | str | None = None) -> Path:
    here = Path(start or __file__).resolve()
    for cand in [here, *here.parents]:
        if (cand / ".git").exists() or (cand / "config").is_dir():
            return cand
    return here


def pinned_version(root: Path) -> str:
    """host.ruff from config/versions.yml when present, else DEFAULT_RUFF."""
    p = root / "config" / "versions.yml"
    if p.is_file():
        try:
            import yaml

            v = (yaml.safe_load(p.read_text()) or {}).get("host", {}).get("ruff")
            if v:
                return str(v)
        except Exception:  # noqa: BLE001, S110 - a missing/unreadable pin is not fatal
            pass
    return DEFAULT_RUFF


def ensure_ruff(root: Path, *, install: bool) -> str:
    exe = shutil.which("ruff")
    if exe:
        return exe
    if not install:
        raise SystemExit("check-ruff: ruff not found; install it (see config/versions.yml)")
    ver = pinned_version(root)
    print(f"check-ruff: ruff missing; installing ruff=={ver}", file=sys.stderr)
    r = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
         f"ruff=={ver}"],
        check=False,
    )
    if r.returncode != 0:
        raise SystemExit(f"check-ruff: could not install ruff=={ver}")
    return shutil.which("ruff") or "ruff"


def run_ruff(root: Path, exe: str) -> list[dict]:
    r = subprocess.run(
        [exe, "check", "--no-cache", "--output-format=json", str(root)],
        capture_output=True, text=True, check=False,
    )
    if r.returncode == 2:
        raise SystemExit(f"check-ruff: ruff failed:\n{r.stderr.strip()[:1000]}")
    try:
        items = json.loads(r.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise SystemExit(f"check-ruff: could not parse ruff output: {exc}; {r.stdout[:200]!r}")
    return items


def signatures(items: list[dict], root: Path) -> Counter:
    """<path>::<rule> counts. Line numbers are deliberately excluded."""
    out: Counter = Counter()
    for it in items:
        f = Path(it.get("filename") or "?")
        try:
            rel = str(f.resolve().relative_to(root.resolve()))
        except (ValueError, OSError):
            rel = f.name
        out[f"{rel}::{it.get('code') or '?'}"] += 1
    return out


def load_baseline(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as exc:
        raise SystemExit(f"check-ruff: baseline {path} is not JSON: {exc}")
    if not isinstance(data, dict):
        raise SystemExit(f"check-ruff: baseline {path} must be an object")
    return data


def compare(current: dict, baseline: dict) -> tuple[dict, list[str]]:
    new = {k: c for k, c in current.items() if c > baseline.get(k, 0)}
    removable = sorted(k for k in baseline if current.get(k, 0) == 0)
    return new, removable


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="check-ruff")
    ap.add_argument("--root", default=None)
    ap.add_argument("--baseline", default=None)
    ap.add_argument("--write-baseline", action="store_true")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-install", action="store_true")
    a = ap.parse_args(argv)

    root = Path(a.root).resolve() if a.root else repo_root()
    baseline_path = Path(a.baseline).resolve() if a.baseline else root / BASELINE_REL
    current = signatures(run_ruff(root, ensure_ruff(root, install=not a.no_install)), root)

    if a.write_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(dict(sorted(current.items())), indent=2) + "\n")
        print(f"check-ruff: wrote {baseline_path} ({sum(current.values())} finding(s))")
        return 0

    baseline = {} if a.strict else load_baseline(baseline_path)
    new, removable = compare(current, baseline)

    if a.json:
        print(json.dumps({"new": new, "removable": removable,
                          "total": sum(current.values()),
                          "baseline": sum(baseline.values())}, indent=2))
    else:
        if new:
            print(f"check-ruff: {len(new)} NEW finding signature(s) vs {baseline_path.name}:",
                  file=sys.stderr)
            for k, c in sorted(new.items()):
                print(f"  {k}  ({c} > {baseline.get(k, 0)})", file=sys.stderr)
            print("fix them, or refresh the baseline deliberately with --write-baseline",
                  file=sys.stderr)
        if removable:
            print(f"check-ruff: {len(removable)} baseline signature(s) gone (remove them to burn down):",
                  file=sys.stderr)
            for k in removable:
                print(f"  {k}", file=sys.stderr)
        if not new:
            print(f"check-ruff: ok — {sum(current.values())} finding(s), none new "
                  f"(baseline {sum(baseline.values())})")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main())
