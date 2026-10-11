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


class PlaidItem(Base):
    """Phase 2: one row per bank login linked through Plaid Link (TD, RBC, CIBC)."""

    __tablename__ = "plaid_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[str] = mapped_column(String(64), unique=True)
    institution_name: Mapped[str | None] = mapped_column(String(255))
    access_token: Mapped[str] = mapped_column(Text)  # encrypted
    transactions_cursor: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=datetime.now)


class SyncRun(Base):
    """One fetch from Gmail, automatic or manual. Shown on the Settings page."""

    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trigger: Mapped[str] = mapped_column(String(16))  # auto / manual / cli
    started_at: Mapped[datetime] = mapped_column(default=datetime.now, index=True)
    finished_at: Mapped[datetime | None]
    ok: Mapped[bool] = mapped_column(default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
