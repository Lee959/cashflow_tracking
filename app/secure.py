"""One-time hardening: Plaid secrets out of .env into the Keychain, and private file permissions.

Run with `python -m app.cli secure-secrets`. Safe to run again: it only moves what's still in .env.
"""

import os
import re
from pathlib import Path

from dotenv import dotenv_values

from app import keychain

# Keychain name -> .env variable names it can come from (the existing .env spells them "SECRETE")
PLAID_SECRETS = {
    "plaid-production-secret": ("PLAID_PRODUCTION_SECRETE", "PLAID_PRODUCTION_SECRET"),
    "plaid-sandbox-secret": ("PLAID_SENDBOX_SECRETE", "PLAID_SANDBOX_SECRET"),
}


def move_secrets_to_keychain(env_file: Path) -> list[str]:
    """Stores each Plaid secret in the Keychain, checks it reads back, then removes its line from
    .env. Returns the variable names that were moved."""
    if not env_file.exists():
        return []
    values = dotenv_values(env_file)
    moved = []
    for keychain_name, variables in PLAID_SECRETS.items():
        for variable in variables:
            if values.get(variable):
                keychain.put(keychain_name, values[variable].strip())  # raises unless it reads back
                moved.append(variable)
    if moved:
        pattern = re.compile(rf"^\s*(export\s+)?({'|'.join(map(re.escape, moved))})\s*=")
        kept = [line for line in env_file.read_text().splitlines(keepends=True) if not pattern.match(line)]
        tmp = env_file.with_suffix(".tmp")
        tmp.write_text("".join(kept))
        os.chmod(tmp, 0o600)
        tmp.replace(env_file)
    return moved


def lock_down(project_root: Path) -> list[str]:
    """Owner-only permissions on .env and the secrets folder. Returns what was changed."""
    changed = []
    targets = [(project_root / ".env", 0o600), (project_root / "secrete", 0o700)]
    targets += [(p, 0o600) for p in (project_root / "secrete").glob("*") if p.is_file()]
    for path, mode in targets:
        if path.exists() and (path.stat().st_mode & 0o777) != mode:
            os.chmod(path, mode)
            changed.append(f"{path.name} -> {oct(mode)[2:]}")
    return changed
