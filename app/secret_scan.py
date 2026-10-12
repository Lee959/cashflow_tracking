"""Finds secrets and personal details before they reach git (and in what's already there).

Two kinds of patterns:
- secrets any project could leak: Plaid tokens and secrets, private keys, Google client secrets,
  and files that must never be committed (.env, the secrets folder, databases)
- your own personal details: config/private_identifiers.txt, one per line, matched ignoring case.
  That file is git-ignored, so the list itself is never committed.

Used by the pre-commit hook (`scan-secrets --staged`) and by `security-check` (whole tree and history).
"""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.config import PROJECT_ROOT

PRIVATE_LIST = PROJECT_ROOT / "config" / "private_identifiers.txt"

SECRET_PATTERNS = {
    "Plaid token": re.compile(r"\b(access|public|link)-(sandbox|development|production)-[0-9a-f]{8}-[0-9a-f]{4}"),
    "Plaid secret": re.compile(r"PLAID_\w*SECRETE?\w*\s*=\s*['\"]?[0-9a-f]{24,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Google client secret": re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}"),
    "Google API key": re.compile(r"AIza[0-9A-Za-z_-]{35}"),
}
FORBIDDEN_FILES = re.compile(r"(^|/)\.env$|^secrete/|\.db$|(^|/)app\.key$|client_secret[^/]*\.json$|private_identifiers")
SKIP_FILES = re.compile(r"package-lock\.json$|\.(png|jpe?g|gif|ico|pdf|woff2?)$")


@dataclass
class Finding:
    where: str  # file, or commit:file
    what: str

    def __str__(self) -> str:
        return f"{self.where}: {self.what}"


def private_identifiers() -> list[str]:
    if not PRIVATE_LIST.exists():
        return []
    lines = (line.strip() for line in PRIVATE_LIST.read_text().splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def scan_text(text: str, private: list[str]) -> list[str]:
    found = [name for name, pattern in SECRET_PATTERNS.items() if pattern.search(text)]
    lower = text.lower()
    found += [f"personal detail “{_mask(item)}”" for item in private if item.lower() in lower]
    return found


def scan_staged() -> list[Finding]:
    """What's about to be committed."""
    private = private_identifiers()
    findings = []
    for path in _git("diff", "--cached", "--name-only", "--diff-filter=ACMR").splitlines():
        if FORBIDDEN_FILES.search(path):
            findings.append(Finding(path, "this file must never be committed"))
            continue
        if SKIP_FILES.search(path):
            continue
        text = _git("show", f":{path}", check=False)
        findings += [Finding(path, what) for what in scan_text(text, private)]
    return findings


def scan_tree() -> list[Finding]:
    """Every file git tracks right now."""
    private = private_identifiers()
    findings = []
    for path in _git("ls-files").splitlines():
        if FORBIDDEN_FILES.search(path):
            findings.append(Finding(path, "tracked by git but must never be committed"))
        elif not SKIP_FILES.search(path) and (PROJECT_ROOT / path).is_file():
            text = (PROJECT_ROOT / path).read_text(errors="ignore")
            findings += [Finding(path, what) for what in scan_text(text, private)]
    return findings


def scan_history(revs: tuple[str, ...] = ("--all",)) -> list[Finding]:
    """Every commit reachable from `revs` (default: all branches, including the GitHub copies):
    file contents, commit messages and author/committer details."""
    private = private_identifiers()
    findings = []
    for commit in _git("rev-list", *revs).split():
        meta = _git("show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit)
        findings += [Finding(f"{commit[:8]} (author/message)", what) for what in scan_text(meta, private)]
        for path in _git("ls-tree", "-r", "--name-only", commit).splitlines():
            if FORBIDDEN_FILES.search(path):
                findings.append(Finding(f"{commit[:8]}:{path}", "must never be committed"))
            elif not SKIP_FILES.search(path):
                text = _git("show", f"{commit}:{path}", check=False)
                findings += [Finding(f"{commit[:8]}:{path}", what) for what in scan_text(text, private)]
    # the same file usually repeats across commits; report each finding once
    return list({str(f): f for f in findings}.values())


def _mask(item: str) -> str:
    return item[:2] + "•" * max(len(item) - 2, 1)


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, errors="ignore")
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout
