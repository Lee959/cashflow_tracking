"""`python -m app.cli security-check`: one command that checks everything the security setup relies on.

Run it now and then (monthly is plenty) and after changing anything about secrets or storage.
"""

import os
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass

from app import auth, keychain, secret_scan
from app.config import ENV_FILE, PROJECT_ROOT, settings
from app.secure import PLAID_SECRETS


@dataclass
class Check:
    ok: bool
    name: str
    detail: str = ""


def run_all(include_audits: bool = True) -> list[Check]:
    checks = [
        *_permissions(),
        *_secrets(),
        _filevault(),
        Check(auth.ENFORCED, "Login (launch link + Touch ID) is enforced",
              "" if auth.ENFORCED else "APP_DISABLE_LOGIN_FOR_TESTS is set outside the tests"),
        _hook(),
        *_git_scans(),
    ]
    if include_audits:
        checks += [_pip_audit(), _npm_audit()]
    return checks


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def _permissions() -> list[Check]:
    targets = [(ENV_FILE, 0o600), (PROJECT_ROOT / "secrete", 0o700), (settings.data_dir, 0o700),
               (settings.db_path, 0o600)]
    targets += [(p, 0o600) for p in (PROJECT_ROOT / "secrete").glob("*") if p.is_file()]
    return [Check(_mode(path) == mode, f"Only you can read {path.name}", f"is {oct(_mode(path))[2:]}, should be {oct(mode)[2:]}")
            for path, mode in targets if path.exists()]


def _secrets() -> list[Check]:
    in_env = [v for variables in PLAID_SECRETS.values() for v in variables if os.getenv(v)]
    plaid_name = "plaid-production-secret" if settings.plaid_env == "production" else "plaid-sandbox-secret"
    data_in_project = (PROJECT_ROOT / "data" / "cashflow.db").exists()
    return [
        Check(keychain.get("encryption-key") is not None, "Encryption key is in the Keychain"),
        Check(not (settings.secrets_dir / "app.key").exists(), "No key file left in the project folder"),
        Check(keychain.get(plaid_name) is not None, f"Plaid {settings.plaid_env} secret is in the Keychain"),
        Check(not in_env, "No Plaid secrets in .env", f"still in .env: {', '.join(in_env)}; run secure-secrets"),
        Check(PROJECT_ROOT not in settings.data_dir.parents and not data_in_project,
              "Data is outside the iCloud-synced project folder", str(settings.data_dir)),
    ]


def _filevault() -> Check:
    try:
        out = subprocess.run(["fdesetup", "status"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return Check(False, "FileVault disk encryption is on", "couldn't check")
    return Check("FileVault is On" in out, "FileVault disk encryption is on", out.strip())


def _hook() -> Check:
    hook = PROJECT_ROOT / ".git" / "hooks" / "pre-commit"
    installed = hook.exists() and "scan-secrets" in hook.read_text()
    return Check(installed, "Pre-commit hook blocks secrets and personal details", "run install-hooks")


def _git_scans() -> list[Check]:
    has_list = bool(secret_scan.private_identifiers())
    tree, history = secret_scan.scan_tree(), secret_scan.scan_history()
    return [
        Check(has_list, "Personal-details list exists (config/private_identifiers.txt)",
              "without it only generic secrets are detected"),
        Check(not tree, "No secrets or personal details in tracked files", "; ".join(map(str, tree[:5]))),
        Check(not history, "No secrets or personal details anywhere in git history", "; ".join(map(str, history[:5]))),
    ]


def _pip_audit() -> Check:
    result = subprocess.run([sys.executable, "-m", "pip_audit", "--progress-spinner", "off"],
                            capture_output=True, text=True, cwd=PROJECT_ROOT)
    out = (result.stdout + result.stderr).strip().splitlines()
    return Check(result.returncode == 0, "Python packages have no known vulnerabilities", out[-1] if out else "")


def _npm_audit() -> Check:
    if not shutil.which("npm"):
        return Check(False, "JavaScript packages have no known vulnerabilities", "npm not found")
    result = subprocess.run(["npm", "audit", "--omit=dev"], capture_output=True, text=True,
                            cwd=PROJECT_ROOT / "frontend")
    lines = result.stdout.strip().splitlines()
    return Check(result.returncode == 0, "JavaScript packages have no known vulnerabilities", lines[-1] if lines else "")
