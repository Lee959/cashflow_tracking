# Cashflow Tracker

A household cash-flow tracking tool. Done so far: **automatically fetching bills from Gmail** (amount, due date, PDF), calculating **monthly fixed costs**, and viewing them by month.
Next phase: connect Plaid (transactions, balances, and liabilities from TD / RBC / CIBC) to capture debits that don't come with an email, and to add cash-flow forecasting and net-worth tracking.

- **Backend**: Python / FastAPI. Serves a JSON API and fetches Gmail bills on a schedule in the background (`app/`)
- **Frontend**: React + TypeScript + Vite, with a liquid-glass style UI (`frontend/`)
- Data is stored in local SQLite (`data/cashflow.db`) and the app only runs on `127.0.0.1`

## Pages

**Dashboard (`/`)**: ordered top to bottom by importance

1. **Spent this month** (the biggest number): the amount of bills already paid this month (due date has passed), with a progress bar showing what share of this month's total due it represents.
   On the right: **how much is still left to pay**, what the next bill is, which day, **how it will be paid**, and whether this month's total is higher or lower than a usual month.
   You can page back through previous months (for a past month it shows the total spent that month), but **future months are not shown**.
2. **This month's bill list**: due date, amount, and payment method for each bill. These can be edited manually, and bills with a PDF can be opened directly.
3. **Bills by month**: bar chart of the last 12 months. Hover to see the amount; click a bar to jump to that month.
4. **Your usual month**: the regular fixed bills in a typical month (monthly average), listed by bill.
   Bimonthly water bills and twice-yearly property tax are all converted to a monthly amount. Formula: each bill's total over the last 12 months ÷ the number of months covered.

Currently only bills found in Gmail are counted; card spending and other bank account outflows will be included once Plaid is connected.

**Payment method** (auto-debit from bank, auto-charge to card, or paid manually) is detected automatically from emails:
- The bill email itself, e.g. Hydro's "Automated Payment Withdrawal Date", or the water bill's "registered with pre-authorized debit"
- Payment-setup confirmation emails from the same company, e.g. Bell's "Confirmation - Change pre-authorized payments", from which the bank or card name and last digits are read (e.g. RBC ••321)

The most recent email wins. Each automatic fetch also checks for new setup emails. For bills whose emails don't say (e.g. property tax), you can set "Paid by" under the bill's Edit; manual settings take priority over emails.
Hover over a payment method to see which email it came from.

Credit card statements are **not counted in the totals**: card spending will come from bank transactions later, which avoids double-counting bills paid by card.

**Settings (`/settings`)**: Gmail connection, auto-fetch status and manual fetch, bill sender management, recent fetch log.
**Find bill senders (`/settings/senders`)**: scan Gmail and tick the senders that actually send bills.

## First-time setup

1. **Python dependencies** (already installed in `.venv`; only needed in a new environment)
   ```bash
   .venv/bin/pip install -r requirements.txt
   ```

2. **Frontend dependencies and build** (requires Node.js, already installed via Homebrew)
   ```bash
   cd frontend && npm install && npm run build
   ```

3. **Google OAuth consent screen** (Google Cloud Console → Google Auth Platform)
   - Make sure the Gmail API is enabled.
   - When the Publishing status is **Testing**: add the Gmail address that receives the bills under *Audience → Test users*, otherwise sign-in will be blocked (error 403 access_denied).
     In Testing mode, authorization **expires after 7 days**; when it does, click Connect Gmail again in Settings.
   - To avoid re-authorizing every week: click **Publish app** to switch to In production. No review submission is needed.
     At sign-in you'll see "Google hasn't verified this app"; click *Advanced → Go to …* to continue (up to 100 users, plenty for a household).
   - The OAuth client is a *Desktop app* type, so the callback URL `http://localhost:8000/auth/google/callback` doesn't need to be registered separately.

4. **Start**
   ```bash
   .venv/bin/python -m app
   ```
   Open http://localhost:8000 → **Settings** → **Connect Gmail** → **Find bill senders**, tick the bill senders → Save.
   After that, fetching happens automatically; no more manual clicks needed.

### Frontend development

Keep `.venv/bin/python -m app` running and open another terminal:
```bash
cd frontend && npm run dev
```
Open http://localhost:5173. Code changes hot-reload, and API requests are proxied to :8000. When done, run `npm run build` and :8000 will serve the new version.
After Google sign-in completes, you'll be redirected back to :8000 (the callback URL is fixed there); this is expected.

## Automatic fetching

- While the web service is running, it fetches this month's and next month's bills in the background **every 6 hours**. The interval can be changed in `.env` with `APP_SYNC_INTERVAL_HOURS`.
  Every fetch is recorded under *Recent fetches* in Settings; the top of the Dashboard only shows a notice when something goes wrong (Gmail not connected or a fetch failed).
- Restarting the service won't trigger extra fetches: if the last fetch was less than 6 hours ago, it's skipped.
- Settings → **Fetch now** fetches immediately. Under **More options** you can backfill the last N months, or use *Re-parse* to re-parse stored emails after changing parsing rules (without contacting Gmail).
- Gmail rate limits are handled by waiting and retrying automatically.
- ⚠ Fetching only happens while the service is running. Nothing is fetched while the computer is off or the service isn't running; it catches up automatically on the next start.

## Bill parsing logic

Each email from a configured sender counts as one bill, assigned to **the month of its due date** (i.e. the month the money is actually withdrawn; if there's no due date, the month the email was received).
When fetching a month, emails received in the previous month are also read, since bills usually arrive 2–3 weeks in advance.

Amount and due date are extracted in this order (both the email body and PDF attachment text are read):
1. `amount_patterns` / `due_date_patterns` written for that sender in `config/bill_senders.toml` (most accurate; add as needed)
2. Generic rules: the amount or date following keywords like `Amount due`, `Total due`, `New balance`, `Due date`, `Withdrawal date`, `Payment date`
3. If the email contains only one amount, that amount is used

Supported formats include: negative credits (e.g. Hydro's `$-5.03`), space-separated dates like `2026 10 09` (Bell), amounts without a `$` sign (water bill), and French-format amounts `1 234,56 $`.

- Notification-only emails without an amount, such as "eStatement is ready", are marked as **statement notice**.
- Others where no amount can be found are marked **needs review** and can be filled in on the web page; manually edited bills are not overwritten by re-parsing.
- Non-bill emails from the same sender (marketing, notifications) are filtered with `subject_includes` / `subject_excludes`.

### How Find bill senders scans

1. Searches Gmail with keywords like bill, statement, amount due, skipping the Promotions category.
2. Excludes system notifications (Google Drive shares, etc.), your own address, personal email addresses, addresses that look like a person's name, and Re:/Fwd: conversations.
3. Groups by sender and determines how often they send: monthly, every two months, a few times a year, or one-off (e.g. a car rental is one-off).
4. Determines the sender type: bill, credit card statement, investment or bank account statement, or purchase/travel receipt.
5. Opens the latest email to check whether it contains an amount.
6. Reminder emails from the same company (e.g. Hydro's "Due date alert") are marked as duplicates and suggested to skip.

## Current coverage

| Bill | Source |
|---|---|
| Hydro Ottawa (electricity) | ✅ Gmail, monthly |
| Ottawa water | ✅ Gmail, every two months |
| Ottawa property tax | ✅ Gmail, twice a year |
| Bell internet | ✅ Gmail, monthly |
| Enbridge gas, Reliance water heater, My Ontario account, Anytime Fitness | ❌ No bill emails in this Gmail |
| Rogers Business Mobile | ❌ Bills may go to another household member's email |

How to fill these gaps:
- **Enbridge**: in Enbridge My Account → My Bill Options, turn on **eBill with PDF attached**; bills will then arrive in Gmail and can be parsed directly.
  My Account → Share My Data also lets you download 24 months of history as Green Button XML.
- **Reliance**: in Ontario this is often combined into the Enbridge bill; check the Enbridge bill first.
- **Rogers**: forward to this Gmail, or support connecting a second Gmail later.
- **Others**: wait for Plaid bank transactions, or enter manually.

## Command line

```bash
.venv/bin/python -m app.cli sync-bills                   # Fetch this month and next (same as auto-fetch)
.venv/bin/python -m app.cli sync-bills --month 2026-09   # Specific month
.venv/bin/python -m app.cli sync-bills --last 12 --force # Re-download the last 12 months
.venv/bin/python -m app.cli list-bills --month 2026-09
.venv/bin/python -m app.cli discover-senders --days 365  # Find senders that look like bills
.venv/bin/python -m app.cli reparse                      # Re-parse after editing bill_senders.toml (no Gmail access)
.venv/bin/python -m app.cli plaid-check                  # Check Plaid credentials
.venv/bin/python -m pytest                               # Backend tests
cd frontend && npm run lint && npm run build             # Frontend lint and build
```

## Project structure

```
app/                      Backend (Python)
  main.py                 FastAPI entry: Google sign-in, PDF attachments, serves frontend build
  api.py                  JSON API (/api/dashboard, /api/settings, /api/sync …)
  scheduler.py            Background auto-fetch + fetch log
  cli.py                  Command line
  config.py               Reads .env
  google_auth.py          Gmail OAuth (refresh token stored encrypted)
  gmail_client.py         Gmail API: search, decode body, download PDFs, rate-limit retry
  bills/
    senders.py            Read/write bill_senders.toml, build Gmail search queries
    parser.py             Amount and due-date extraction
    service.py            Monthly sync, re-parse, queries
    dashboard.py          Dashboard data: fixed costs, monthly summary, 12-month trend
    discover.py           Find bill senders scanning and classification
  plaid_client.py         Plaid (connection test only for now)
  models.py               Database tables (SQLite)
frontend/                 Frontend (React + TypeScript + Vite)
  src/pages/              Dashboard, Settings, FindSenders
  src/components/         FixedCosts, MonthlyChart, BillsTable, TopBar
  src/styles.css          Liquid-glass styles (light and dark mode)
config/bill_senders.example.toml
tests/                    Backend tests (pytest)
```

## Security

- The service only listens on `127.0.0.1` and has no login, so **do not deploy it to the public internet**.
- The Gmail token is encrypted with `secrete/app.key` before being stored in the database; `.env`, `secrete/`, `data/`, and `config/bill_senders.toml` are all in `.gitignore`.
- Gmail access is read-only (`gmail.readonly`), and fetching only searches configured senders.

## Plaid status (dashboard checked 2026-10-11)

- Production application under review; the **Security questionnaire** is still outstanding (3 items to fill in under App profile in the Compliance Center).
- Requested: Transactions, Recurring Transactions, Transactions Refresh, Investments, Liabilities, Enrich.
- **Statements (bank statement PDFs) only support US banks**, so TD / RBC / CIBC can't use it — that's why bills come from Gmail.
- Sandbox credentials work. Until Production is approved, develop with `PLAID_ENV=sandbox` (the default).

## Next steps

- [ ] Turn on Enbridge eBill with PDF; forward Rogers bills to this Gmail
- [ ] Manually enter fixed costs that have no bill emails (Anytime Fitness, My Ontario, etc.)
- [ ] Green Button XML import (to backfill Enbridge and Hydro history)
- [ ] Start the service automatically at boot (macOS launchd), so fetching runs whenever the computer is on
- [ ] Plaid Link integration → transaction sync, to verify bills were actually debited and to capture debits without emails
- [ ] Daily balance snapshots (Plaid doesn't provide historical balances, so the earlier the better)
- [ ] 30–60 day cash-flow forecast
