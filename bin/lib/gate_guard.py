#!/usr/bin/env python3
"""gate_guard — the content checks GitHub cannot express (#735).

Two things the platform does not enforce for us, evaluated on the PR payload:

* **skip directives** — GitHub Actions honours ``[skip ci]``/``[ci skip]``/… in a
  commit message by *not running* workflows. We forbid them in the PR title, body,
  or any commit, so a required check can never be silently skipped.
* **authorized authors** — only identities listed in
  ``config/authorized-actors.yml`` may open a pull request (default-deny). This is
  the command-and-control allowlist.

Deliberately **not** here: human approval for protected paths. GitHub enforces
that natively with ``require_code_owner_review`` + CODEOWNERS and an org ruleset
``required_reviewers`` rule (file patterns). Duplicating it in a check made the
result depend on *when* the check last ran, so an approval needed a re-run —
fragile and wrong. Platform controls for platform concerns.

Unit-tested in ``tests/test_gate_guard.py``.
"""
from __future__ import annotations

import re

SKIP_RE = re.compile(
    r"\[(?:skip[ _-]?(?:ci|actions)|ci[ _-]?skip|no[ _-]?ci|actions[ _-]?skip)\]"
    r"|\*\*\*NO_CI\*\*\*",
    re.I,
)

_BOTS = {"dependabot", "dependabot[bot]", "github-actions",
         "github-actions[bot]", "github-actions-bot"}


def find_skip_directives(text: str) -> list[str]:
    """Every skip directive found in ``text`` (case-insensitive)."""
    return [m.group(0) for m in SKIP_RE.finditer(text or "")]


def is_human(login: str) -> bool:
    """A human actor: not an App (`app/...`) and not a `[bot]` login."""
    l = (login or "").strip().lower()
    if not l or l in _BOTS:
        return False
    return not (l.endswith("[bot]") or l.startswith("app/"))


def authorized(login: str, policy: dict) -> bool:
    """Command-and-control allowlist (default-deny)."""
    if not login:
        return False
    allowed = set(policy.get("humans", []) or []) | set(policy.get("agents", []) or [])
    return login in allowed


def commit_texts(commits) -> dict[str, str]:
    """Map ``commit <sha>`` -> full message for each commit."""
    out: dict[str, str] = {}
    for c in commits or []:
        sha = (c.get("oid", "?") or "?")[:8]
        out[f"commit {sha}"] = ((c.get("messageHeadline", "") or "") + "\n"
                                + (c.get("messageBody", "") or ""))
    return out


def evaluate(*, title: str, body: str, commits, author: str, policy: dict) -> list[str]:
    """Return the list of policy violations (empty == pass)."""
    violations: list[str] = []

    # R1 — no skip directives anywhere that affects what runs.
    fields = {"PR title": title or "", "PR body": body or ""}
    fields.update(commit_texts(commits))
    for label, text in fields.items():
        for d in find_skip_directives(text):
            violations.append(f"{label}: forbidden skip directive {d!r}")

    # R2 — only authorized identities may open a PR.
    if not authorized(author, policy):
        violations.append(
            f"author {author or '(unknown)'!r} is not authorized "
            "(config/authorized-actors.yml; default-deny)"
        )
    return violations
