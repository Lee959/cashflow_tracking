from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class OAuthToken(Base):
    """One row per connected account. `token_json` is encrypted (see app/crypto.py)."""

    __tablename__ = "oauth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), unique=True)  # "google"
    account_email: Mapped[str | None] = mapped_column(String(255))
    token_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)


class BillEmail(Base):
    """A raw email from a configured bill sender. Kept so bills can be re-parsed offline."""

    __tablename__ = "bill_emails"

    id: Mapped[int] = mapped_column(primary_key=True)
    gmail_message_id: Mapped[str] = mapped_column(String(64), unique=True)
    thread_id: Mapped[str] = mapped_column(String(64))
    sender_email: Mapped[str] = mapped_column(String(255), index=True)
    sender_name: Mapped[str | None] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(index=True)
    snippet: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    attachment_text: Mapped[str] = mapped_column(Text, default="")
    fetched_at: Mapped[datetime] = mapped_column(default=datetime.now)

    attachments: Mapped[list["Attachment"]] = relationship(back_populates="email", cascade="all, delete-orphan")
    bill: Mapped["Bill | None"] = relationship(back_populates="email", cascade="all, delete-orphan")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("bill_emails.id"))
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(100))
    path: Mapped[str] = mapped_column(Text)  # relative to data/
    size: Mapped[int]

    email: Mapped[BillEmail] = relationship(back_populates="attachments")


class Bill(Base):
    """The structured bill extracted from one email.

    status:
      parsed        amount found automatically
      statement     "eStatement is ready" email: real monthly statement, amount only in online banking
      needs_review  email matched a biller but no amount was found
      ignored       subject didn't match the biller's subject filters (e.g. marketing)
      manual        edited by hand; re-parsing won't overwrite it
    """

    __tablename__ = "bills"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("bill_emails.id"), unique=True)
    biller: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(64), default="other")
    month: Mapped[str] = mapped_column(String(7), index=True)  # YYYY-MM the email arrived in
    amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(3), default="CAD")
    due_date: Mapped[date | None]
    status: Mapped[str] = mapped_column(String(16), default="needs_review")
    parse_notes: Mapped[str] = mapped_column(Text, default="")
    # how the bill gets paid, when the email says so (see parser.detect_payment)
    payment_method: Mapped[str | None] = mapped_column(String(16))
    payment_detail: Mapped[str | None] = mapped_column(String(64))

    email: Mapped[BillEmail] = relationship(back_populates="bill")


class BillerPayment(Base):
    """How a biller gets paid, beyond what its bill emails say: found in the biller's
    payment-setup emails (e.g. Bell's "Change pre-authorized payments"), or set by hand."""

    __tablename__ = "biller_payments"

    biller: Mapped[str] = mapped_column(String(255), primary_key=True)
    email_method: Mapped[str | None] = mapped_column(String(16))
    email_detail: Mapped[str | None] = mapped_column(String(64))
    email_date: Mapped[datetime | None]
    email_subject: Mapped[str | None] = mapped_column(Text)
    email_message_id: Mapped[str | None] = mapped_column(String(64))
    user_method: Mapped[str | None] = mapped_column(String(16))
    user_detail: Mapped[str | None] = mapped_column(String(64))


class ManualCost(Base):
    """A recurring fixed cost entered by hand, for payments that never send a bill email.

    frequency: monthly / every 2 months / quarterly / twice a year / yearly (see app/bills/manual.py)
    due_day: day of the month it's paid (mostly for monthly costs)
    next_due_date: any one due date; later ones are worked out from the frequency
    active: False = paused, not counted in the fixed bills
    """

    __tablename__ = "manual_costs"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(64), default="other")
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    frequency: Mapped[str] = mapped_column(String(16), default="monthly")
    due_day: Mapped[int | None]
    next_due_date: Mapped[date | None]
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class PlaidItem(Base):
    """One bank login connected through Plaid Link (TD, RBC, CIBC). access_token is encrypted."""

    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), unique=True)
    institution_name: Mapped[str | None] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(Text)  # encrypted
    transactions_cursor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    institution_id: Mapped[str | None] = mapped_column(String(64))
    environment: Mapped[str | None] = mapped_column(String(16))  # sandbox / production: never mix their data
    last_synced_at: Mapped[datetime | None]
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_code: Mapped[str | None] = mapped_column(String(64))  # e.g. ITEM_LOGIN_REQUIRED -> Reconnect
    products: Mapped[str | None] = mapped_column(Text)  # comma-separated Plaid products on this connection


class PlaidAccount(Base):
    """A bank or credit card account under a PlaidItem, with its latest balances."""

    __tablename__ = "plaid_accounts"

    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255))
    official_name: Mapped[str | None] = mapped_column(String(255))
    mask: Mapped[str | None] = mapped_column(String(8))
    type: Mapped[str] = mapped_column(String(16))  # depository / credit / loan / investment
    subtype: Mapped[str | None] = mapped_column(String(32))
    currency: Mapped[str | None] = mapped_column(String(3))
    current_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    available_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    credit_limit: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    updated_at: Mapped[datetime] = mapped_column(default=datetime.now, onupdate=datetime.now)
    # credit cards, from Plaid Liabilities
    statement_balance: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    statement_date: Mapped[date | None]
    minimum_payment: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    payment_due_date: Mapped[date | None]
    is_overdue: Mapped[bool | None]


class PlaidTransaction(Base):
    """A bank or card transaction. amount follows Plaid: positive = money out, negative = money in."""

    __tablename__ = "plaid_transactions"

    transaction_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    item_id: Mapped[str] = mapped_column(String(64), index=True)
    transaction_date: Mapped[date] = mapped_column("date", index=True)
    name: Mapped[str] = mapped_column(Text)
    merchant_name: Mapped[str | None] = mapped_column(String(255))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    currency: Mapped[str | None] = mapped_column(String(3))
    category: Mapped[str | None] = mapped_column(String(64))  # personal_finance_category.primary
    category_detailed: Mapped[str | None] = mapped_column(String(128))
    pending: Mapped[bool] = mapped_column(default=False)
    payment_channel: Mapped[str | None] = mapped_column(String(16))


class SyncRun(Base):
    """One fetch from Gmail, automatic or manual. Shown on the Settings page."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # auto / manual / cli
    started_at: Mapped[datetime] = mapped_column(default=datetime.now, index=True)
    finished_at: Mapped[datetime | None]
    ok: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text, default="")


class WebSession(Base):
    """A browser signed in through the launch link (see app/auth.py). The cookie holds a random
    token; only its SHA-256 is stored, so the database alone can't be used to sign in."""

    __tablename__ = "web_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    expires_at: Mapped[datetime]
    unlocked_at: Mapped[datetime | None]  # last Touch ID unlock; None = locked
    last_seen_at: Mapped[datetime | None]
    challenge: Mapped[str | None] = mapped_column(Text)  # pending WebAuthn challenge (base64url)


class Passkey(Base):
    """A Touch ID passkey that can unlock the app (WebAuthn, stays in this Mac's Secure Enclave)."""

    __tablename__ = "passkeys"

    credential_id: Mapped[str] = mapped_column(String(512), primary_key=True)  # base64url
    public_key: Mapped[bytes]
    sign_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)
    last_used_at: Mapped[datetime | None]
