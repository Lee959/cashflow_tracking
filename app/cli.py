"""Command line: python -m app.cli <command>

  sync-bills [--month YYYY-MM | --last N] [--force]   fetch bills from Gmail (default: this month and next)
  list-bills [--month YYYY-MM]                          print stored bills
  discover-senders [--days N]                           find senders that look like bills
  reparse                                               re-run the parser on stored emails
  plaid-check                                           verify Plaid credentials (creates a link token)
"""

import argparse
import sys

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

    except (google_auth.GmailNotConnected, BillerConfigError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
