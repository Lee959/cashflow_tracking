"""Command line: python -m app.cli <command>

  sync-bills [--month YYYY-MM | --last N] [--force]   fetch bills from Gmail (default: this month and next)
  list-bills [--month YYYY-MM]                          print stored bills
  discover-senders [--days N]                           find senders that look like bills
  reparse                                               re-run the parser on stored emails
  plaid-check                                           verify Plaid credentials (creates a link token)
  plaid-sync                                            refresh balances and transactions for connected banks
  secure-secrets                                        move Plaid secrets from .env to the Keychain; lock down files
  reset-login                                           forget Touch ID passkeys and sessions (new Mac, reset Touch ID)
  security-check [--quick]                              check permissions, Keychain, login, git, dependencies
  scan-secrets [--staged | --history]                   look for secrets and personal details (pre-commit hook uses --staged)
  install-hooks                                         install the git pre-commit hook
"""

import argparse
import sys
from pathlib import Path

from app import google_auth, scheduler
from app.bills import service
from app.bills.senders import BillerConfigError
from app.db import init_db


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync-bills", help="fetch bills from Gmail")
    sync.add_argument("--month", help="YYYY-MM (default: this month)")
    sync.add_argument("--last", type=int, help="fetch the last N months, ending this month")
    sync.add_argument("--force", action="store_true", help="re-download emails already stored")

    ls = sub.add_parser("list-bills", help="print stored bills")
    ls.add_argument("--month", help="YYYY-MM (default: this month)")

    disc = sub.add_parser("discover-senders", help="list Gmail senders that look like they send bills")
    disc.add_argument("--days", type=int, default=120)

    sub.add_parser("reparse", help="re-run the parser on stored emails")
    sub.add_parser("plaid-check", help="verify Plaid credentials")
    sub.add_parser("plaid-sync", help="refresh balances and transactions for connected banks")
    sub.add_parser("secure-secrets", help="move Plaid secrets from .env to the Keychain; lock down files")
    sub.add_parser("reset-login", help="forget Touch ID passkeys and sessions")
    check = sub.add_parser("security-check", help="check the whole security setup")
    check.add_argument("--quick", action="store_true", help="skip the dependency vulnerability audits")
    scan = sub.add_parser("scan-secrets", help="look for secrets and personal details")
    scan.add_argument("--staged", action="store_true", help="only what's about to be committed")
    scan.add_argument("--history", action="store_true", help="every commit, including authors and messages")
    sub.add_parser("install-hooks", help="install the git pre-commit hook")

    args = parser.parse_args(argv)
    init_db()

    try:
        if args.command == "sync-bills":
            months = [args.month] if args.month else None  # default: this month and next, like the app
            if args.last:
                months = [service.shift_month(service.current_month(), -i) for i in reversed(range(args.last))]
            run = scheduler.run_sync("cli", months, force=args.force)
            print(run.summary)
            return 0 if run.ok else 1

        elif args.command == "list-bills":
            month = args.month or service.current_month()
            bills = service.bills_for_month(month)
            for b in bills:
                amount = f"{b.amount:>10,.2f}" if b.amount is not None else "         ?"
                due = b.due_date.isoformat() if b.due_date else "          "
                print(f"{b.email.received_at:%Y-%m-%d}  {b.biller[:24]:<24} {amount}  due {due}  "
                      f"{b.status:<12} {b.email.subject[:60]}")
            if not bills:
                print(f"No bills stored for {month}.")

        elif args.command == "discover-senders":
            from app.bills.discover import discover_senders

            result = discover_senders(args.days)
            for title, group in [("Bills", result.bills), ("Bills a few times a year", result.periodic_bills),
                                 ("Credit card statements", result.card_statements), ("Not bills", result.not_bills)]:
                print(f"\n{title} ({len(group)})")
                for c in group:
                    found = f"${c.sample_amount:,.2f}" if c.sample_amount else (c.content or "-")
                    note = f"  (reminder for {c.duplicate_of})" if c.duplicate_of else ""
                    print(f"  {c.email:<42} {c.cadence_label:<28} {found:<17} {c.latest_subject[:50]}{note}")
            print(f"\nSkipped: {result.skipped}")
            print("Add the real bill senders at http://localhost:8000/senders or in config/bill_senders.toml")

        elif args.command == "reparse":
            result = service.reparse_all()
            print(result.summary())

        elif args.command == "plaid-check":
            from app import plaid_client
            from app.config import settings

            plaid_client.create_link_token()
            print(f"Plaid {settings.plaid_env} credentials work (link token created).")

        elif args.command == "secure-secrets":
            from app import secure
            from app.config import ENV_FILE, PROJECT_ROOT

            moved = secure.move_secrets_to_keychain(ENV_FILE)
            print(f"Moved to the Keychain: {', '.join(moved)}" if moved else "No Plaid secrets left in .env")
            changed = secure.lock_down(PROJECT_ROOT)
            print(f"Permissions: {', '.join(changed)}" if changed else "Permissions already private")

        elif args.command == "security-check":
            from app import security_check

            checks = security_check.run_all(include_audits=not args.quick)
            for c in checks:
                print(f"  {'✓' if c.ok else '✗'} {c.name}" + ("" if c.ok or not c.detail else f"  ({c.detail})"))
            failed = sum(not c.ok for c in checks)
            print(f"\n{len(checks) - failed} passed, {failed} need attention")
            return 1 if failed else 0

        elif args.command == "scan-secrets":
            from app import secret_scan

            findings = (secret_scan.scan_staged() if args.staged
                        else secret_scan.scan_history() if args.history else secret_scan.scan_tree())
            for f in findings:
                print(f"  ✗ {f}")
            if findings:
                print("\nRemove these before committing (or add the file to .gitignore).", file=sys.stderr)
                return 1
            print("No secrets or personal details found.")

        elif args.command == "install-hooks":
            hook = Path(".git/hooks/pre-commit")
            hook.write_text('#!/bin/sh\n# Blocks commits that contain secrets or personal details (app/secret_scan.py).\n'
                            'cd "$(git rev-parse --show-toplevel)" && exec .venv/bin/python -m app.cli scan-secrets --staged\n')
            hook.chmod(0o755)
            print(f"Installed {hook}")

        elif args.command == "reset-login":
            from app import auth

            removed = auth.reset()
            print(f"Removed {removed} passkey(s) and all sessions. Start the app to set up Touch ID again.")

        elif args.command == "plaid-sync":
            from app import banks

            for r in banks.sync_all() or [{"note": "no banks connected yet"}]:
                print(r)

    except (google_auth.GmailNotConnected, BillerConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
