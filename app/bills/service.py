"""Monthly bill sync: Gmail -> bill_emails/attachments -> bills."""

import io
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import gmail_client, google_auth
from app.bills.parser import detect_payment, parse_bill
from app.bills.senders import Biller, find_biller, gmail_query, load_billers
from app.config import settings
from app.db import session_scope
from app.models import Attachment, Bill, BillEmail, BillerPayment

# Emails where a company confirms how you pay it (Bell: "Confirmation - Change pre-authorized payments")
PAYMENT_SETUP_QUERY = (
    '(subject:"pre-authorized" OR subject:"pre-authorised" OR subject:"automatic payment" OR '
    'subject:"autopay" OR subject:"auto pay" OR subject:"payment method" OR subject:"payment information")'
)

PDF_MAX_PAGES = 5
# Card statements total up spending that's tracked elsewhere (and may include bills paid by card);
# investment statements aren't spending at all. Neither counts toward the bills total.
NOT_IN_TOTALS = ("credit_card", "investment")


@dataclass
class SyncResult:
    month: str
    found: int = 0
    new: int = 0
    skipped: int = 0
    parsed: int = 0
    statements: int = 0
    needs_review: int = 0
    ignored: int = 0

    def summary(self) -> str:
        counts = (f"{self.parsed} with amount, {self.statements} statement notices (no amount), "
                  f"{self.needs_review} need review, {self.ignored} ignored")
        if self.month == "all":
            return f"Re-parsed {self.found} stored emails: {counts}"
        return (f"{self.month}: {self.found} emails found, {self.new} new, {self.skipped} already synced; "
                f"{counts}")


def current_month() -> str:
    return datetime.now(settings.timezone).strftime("%Y-%m")


def shift_month(month: str, delta: int) -> str:
    year, mon = map(int, month.split("-"))
    index = year * 12 + (mon - 1) + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def month_bounds(month: str) -> tuple[datetime, datetime]:
    year, mon = map(int, month.split("-"))
    start = datetime(year, mon, 1, tzinfo=settings.timezone)
    next_year, next_mon = map(int, shift_month(month, 1).split("-"))
    return start, datetime(next_year, next_mon, 1, tzinfo=settings.timezone)


def sync_month(month: str, force: bool = False) -> SyncResult:
    """Fetches the bills for `month` (YYYY-MM).

    Bills belong to the month they're due in, and usually arrive 2-3 weeks earlier, so this
    reads emails received from the start of the previous month to the end of this one.
    """
    billers = load_billers(settings.bill_senders_file)
    service = gmail_client.build_service(google_auth.get_credentials())
    start, _ = month_bounds(shift_month(month, -1))
    _, end = month_bounds(month)
    message_ids = gmail_client.search_message_ids(service, gmail_query(billers, start, end))

    result = SyncResult(month=month, found=len(message_ids))
    with session_scope() as s:
        existing = {
            row.gmail_message_id: row
            for row in s.scalars(
                select(BillEmail)
                .where(BillEmail.gmail_message_id.in_(message_ids))
                .options(selectinload(BillEmail.bill))
            )
        }
        for message_id in message_ids:
            old = existing.get(message_id)
            # force re-downloads, but never throws away a bill you edited by hand
            if old is not None and (not force or (old.bill and old.bill.status == "manual")):
                result.skipped += 1
                continue
            msg = gmail_client.fetch_message(service, message_id)
            biller = find_biller(billers, msg.sender_email)
            if biller is None:  # Gmail's from: search is looser than our exact matching
                result.found -= 1
                continue
            if old is not None:
                s.delete(old)
                s.flush()
            email = _store_email(s, msg)
            result.new += 1
            _count(result, _apply_parse(s, email, biller))
    return result


def refresh_payment_methods() -> int:
    """Looks for each biller's latest payment-setup email and records the method it confirms.

    Searches the biller's whole domain (Bell's setup emails come from noreply@bell.ca, its bills
    from ebill@bell.ca), except when several billers share a domain (City of Ottawa water and
    property tax): then only the biller's own addresses count. Returns how many billers changed.
    """
    billers = load_billers(settings.bill_senders_file)
    service = gmail_client.build_service(google_auth.get_credentials())
    domain_owners: dict[str, set[str]] = {}
    for b in billers:
        for sender in b.senders:
            domain_owners.setdefault(_base_domain(sender), set()).add(b.name)

    updated = 0
    with session_scope() as s:
        for b in billers:
            sources = sorted({
                _base_domain(sender) if len(domain_owners[_base_domain(sender)]) == 1 else sender.lstrip("@")
                for sender in b.senders
            })
            ids = gmail_client.search_message_ids(
                service, f"from:({' OR '.join(sources)}) newer_than:730d {PAYMENT_SETUP_QUERY}"
            )
            row = s.get(BillerPayment, b.name)
            if not ids or (row and row.email_message_id == ids[0]):  # newest first; nothing new
                continue
            msg = gmail_client.fetch_message(service, ids[0])
            if row is None:
                row = BillerPayment(biller=b.name)
                s.add(row)
            row.email_message_id = ids[0]
            if found := detect_payment(f"{msg.subject}\n{msg.text}"):
                row.email_method, row.email_detail = found
                row.email_subject = msg.subject
                row.email_date = msg.received_at.astimezone(settings.timezone).replace(tzinfo=None)
                updated += 1
    return updated


def set_payment_method(biller: str, method: str | None, detail: str | None) -> None:
    """Your own setting for how a biller is paid; method=None goes back to what the emails say."""
    with session_scope() as s:
        row = s.get(BillerPayment, biller)
        if row is None:
            row = BillerPayment(biller=biller)
            s.add(row)
        row.user_method = method
        row.user_detail = (detail or "").strip() or None if method else None


def reparse_all() -> SyncResult:
    """Re-runs the parser on stored emails (no Gmail calls). Use after editing bill_senders.toml."""
    billers = load_billers(settings.bill_senders_file)
    result = SyncResult(month="all")
    with session_scope() as s:
        for email in s.scalars(select(BillEmail).options(selectinload(BillEmail.bill))):
            result.found += 1
            biller = find_biller(billers, email.sender_email)
            if biller is None:
                status = _mark_unconfigured(s, email)
            else:
                status = _apply_parse(s, email, biller)
            _count(result, status)
    return result


def bills_for_month(month: str) -> list[Bill]:
    with session_scope() as s:
        stmt = (
            select(Bill)
            .where(Bill.month == month)
            .options(selectinload(Bill.email).selectinload(BillEmail.attachments))
            .join(Bill.email)
            .order_by(Bill.status == "ignored", Bill.due_date.is_(None), Bill.due_date, BillEmail.received_at)
        )
        return list(s.scalars(stmt))


def counted_bills(first_month: str, last_month: str) -> list[Bill]:
    """Bills with an amount that count toward totals, due between the two months (inclusive)."""
    with session_scope() as s:
        stmt = (
            select(Bill)
            .where(
                Bill.month.between(first_month, last_month),
                Bill.status != "ignored",
                Bill.category.not_in(NOT_IN_TOTALS),
                Bill.amount.is_not(None),
            )
            .order_by(Bill.due_date)
        )
        return list(s.scalars(stmt))


def earliest_bill_month() -> str | None:
    with session_scope() as s:
        return s.scalar(select(func.min(Bill.month)).where(Bill.status != "ignored"))


def monthly_totals(end_month: str, months: int = 12) -> list[tuple[str, Decimal]]:
    first = shift_month(end_month, -(months - 1))
    with session_scope() as s:
        rows = dict(
            s.execute(
                select(Bill.month, func.sum(Bill.amount))
                .where(
                    Bill.month.between(first, end_month),
                    Bill.status != "ignored",
                    Bill.category.not_in(NOT_IN_TOTALS),
                )
                .group_by(Bill.month)
            ).all()
        )
    return [(m, Decimal(rows.get(m) or 0)) for m in (shift_month(first, i) for i in range(months))]


def update_bill(bill_id: int, amount: Decimal | None, due_date: date | None, ignored: bool = False) -> None:
    with session_scope() as s:
        bill = s.get(Bill, bill_id)
        if bill is None:
            raise ValueError(f"Bill {bill_id} not found")
        bill.amount = amount
        bill.due_date = due_date
        bill.month = _bill_month(due_date, bill.email.received_at)
        bill.status = "ignored" if ignored else "manual"
        bill.parse_notes = "edited by hand"


def get_attachment(attachment_id: int) -> Attachment | None:
    with session_scope() as s:
        return s.get(Attachment, attachment_id)


def _store_email(s: Session, msg: gmail_client.FetchedMessage) -> BillEmail:
    received_local = msg.received_at.astimezone(settings.timezone).replace(tzinfo=None)
    email = BillEmail(
        gmail_message_id=msg.id,
        thread_id=msg.thread_id,
        sender_email=msg.sender_email,
        sender_name=msg.sender_name,
        subject=msg.subject,
        received_at=received_local,
        snippet=msg.snippet,
        body_text=msg.text,
    )
    pdf_texts = []
    folder = settings.attachments_dir / msg.id
    for att in msg.attachments:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / _safe_filename(att.filename)
        path.write_bytes(att.data)
        email.attachments.append(
            Attachment(
                filename=att.filename,
                mime_type=att.mime_type,
                path=str(path.relative_to(settings.data_dir)),
                size=len(att.data),
            )
        )
        pdf_texts.append(pdf_text(att.data))
    email.attachment_text = gmail_client.normalize_whitespace("\n\n".join(pdf_texts))
    s.add(email)
    s.flush()
    return email


def _apply_parse(s: Session, email: BillEmail, biller: Biller) -> str:
    bill = email.bill
    if bill is None:
        bill = Bill(email_id=email.id)
        s.add(bill)
        email.bill = bill
    bill.biller = biller.name
    bill.category = biller.category
    bill.currency = biller.currency
    text = "\n".join([email.subject, email.body_text, email.attachment_text])
    bill.payment_method, bill.payment_detail = detect_payment(text) or (None, None)
    if bill.status == "manual":
        return bill.status

    if not biller.accepts_subject(email.subject):
        bill.amount, bill.due_date = None, None
        bill.month = _bill_month(None, email.received_at)
        bill.status = "ignored"
        bill.parse_notes = "subject didn't match subject_includes/subject_excludes"
        return bill.status

    parsed = parse_bill(text, biller)
    bill.amount = parsed.amount
    bill.due_date = parsed.due_date
    bill.month = _bill_month(parsed.due_date, email.received_at)
    if parsed.amount is not None:
        bill.status = "parsed"
    else:
        bill.status = "statement" if parsed.statement_notice else "needs_review"
    bill.parse_notes = "; ".join(parsed.notes)
    return bill.status


def _base_domain(sender: str) -> str:
    return ".".join(sender.rsplit("@", 1)[-1].split(".")[-2:])


def _bill_month(due_date: date | None, received_at: datetime) -> str:
    """Bills count in the month the money leaves: the due date, else when the email arrived."""
    return (due_date or received_at).strftime("%Y-%m")


def _mark_unconfigured(s: Session, email: BillEmail) -> str:
    if email.bill is not None and email.bill.status != "manual":
        email.bill.status = "ignored"
        email.bill.parse_notes = "sender no longer in bill_senders.toml"
        return "ignored"
    return email.bill.status if email.bill else "ignored"


def _count(result: SyncResult, status: str) -> None:
    if status in ("parsed", "manual"):
        result.parsed += 1
    elif status == "statement":
        result.statements += 1
    elif status == "needs_review":
        result.needs_review += 1
    else:
        result.ignored += 1


def pdf_text(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            return "[password-protected PDF]"
        return "\n".join(page.extract_text() or "" for page in reader.pages[:PDF_MAX_PAGES])
    except Exception as exc:  # malformed PDFs shouldn't stop the sync
        return f"[could not read PDF: {exc}]"


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^\w.\- ]", "_", name).strip(" .") or "attachment.pdf"
    return cleaned[:120]
