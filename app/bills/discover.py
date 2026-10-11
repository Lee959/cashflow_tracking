"""Finds Gmail senders that look like bills, so they can be added to bill_senders.toml.

1. Search for bill-like emails (subject/body keywords), skipping promotions.
2. Drop what is never a bill: system notifications, your own address, people (gmail.com etc.)
   and conversations (Re:/Fwd:).
3. Group by sender, work out how often it writes (monthly, every 2 months, a few times a year)
   and what kind of sender it is: a service bill, a credit card statement, an investment/bank
   account statement, or a purchase/travel receipt.
4. Open the latest email of each likely bill to see whether it actually states an amount.
"""

import re
import statistics
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parseaddr

from app import gmail_client, google_auth
from app.bills.parser import STATEMENT_NOTICE_RE, parse_bill
from app.bills.senders import Biller
from app.bills.service import pdf_text
from app.config import settings

DEFAULT_DAYS = 365
MAX_MESSAGES = 800
BATCH_SIZE = 50  # Gmail asks for <= 50 calls per batch request
CACHE_SECONDS = 300

SUBJECT_WORDS = [
    "bill", "statement", "estatement", "e-statement", "invoice", "payment due", "amount due",
    "payment reminder", "pre-authorized", "preauthorized", "autopay", "auto-pay", "premium",
    "renewal", "facture", "relevé",
]
BODY_PHRASES = ["amount due", "payment due", "due date", "minimum payment", "new balance", "total due"]

# Senders that are never bills: sharing/calendar/security notifications, social networks.
IGNORED_SENDERS = re.compile(
    r"^(drive-shares[\w-]*|comments-noreply|calendar-notification|forms-receipts-noreply|"
    r"no-reply@accounts|noreply@photos|meet-recordings-noreply)@"
    r"|@(docs\.google\.com|accounts\.google\.com|facebookmail\.com|linkedin\.com|instagram\.com|"
    r"x\.com|twitter\.com|github\.com)$",
    re.I,
)
# "Jane (via Google Drive)", "Jane（通过Google 云端硬盘发送）"
IGNORED_DISPLAY_NAMES = re.compile(r"\(via |（通过|\(par l'entremise", re.I)
# Mailboxes that belong to people, not companies
PERSONAL_DOMAINS = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com", "msn.com", "yahoo.com",
    "yahoo.ca", "icloud.com", "me.com", "aol.com", "protonmail.com", "proton.me", "qq.com", "163.com",
    "126.com", "sympatico.ca",
}
CONVERSATION_SUBJECT = re.compile(r"^\s*(re|fw|fwd|回复|转发|答复)\s*[:：]", re.I)


def is_person(name: str, email: str) -> bool:
    """'Jane Doe <jane_doe@work.example>': a person's own mailbox, even at a company domain."""
    letters = lambda s: re.sub(r"[^a-z]", "", s.lower())  # noqa: E731
    return len(name.split()) >= 2 and len(letters(name)) >= 5 and letters(name) in letters(email.split("@")[0])

# Kinds of sender
BILL, CARD, ACCOUNT, PURCHASE = "bill", "card", "account", "purchase"
KIND_LABELS = {
    BILL: "bill",
    CARD: "card statement",
    ACCOUNT: "investment / bank account statement",
    PURCHASE: "purchase / travel",
}
ACCOUNT_WORDS = re.compile(
    r"wealth|invest|retire|savings|rrsp|tfsa|fhsa|\bresp\b|portfolio|brokerage|financial planning|"
    r"pension|mutual fund|chequing",
    re.I,
)
CARD_WORDS = re.compile(r"credit card|mastercard|\bvisa\b|amex|american express|line of credit", re.I)
PURCHASE_WORDS = re.compile(
    r"receipt|reservation|booking|itinerary|your order|order confirm|statement of charges|rental|"
    r"e-return|shipped|delivery",
    re.I,
)

# Checked in this order; credit_card first so "Rogers Bank" isn't taken for Rogers the phone company.
CATEGORY_HINTS = {
    "credit_card": ["td", "rbc", "cibc", "bmo", "scotia", "amex", "american express", "mastercard", "visa",
                    "capital one", "tangerine", "pc financial", "triangle", "ctfs", "rogers bank", "rogersbank"],
    "utilities": ["hydro", "enbridge", "energy", "utilit", "water", "electric", "power", "reliance"],
    "insurance": ["insur", "assur", "intact", "desjardins", "aviva", "belair", "sonnet", "cooperators",
                  "allstate", "manulife", "sunlife", "sun life"],
    "phone_internet": ["rogers", "bell", "telus", "fido", "koodo", "virgin", "freedom", "videotron",
                       "chatr", "public mobile", "lucky", "teksavvy", "start.ca", "distributel"],
    "housing": ["property tax", "rent", "property", "apartment", "realty", "condo", "landlord", "mortgage",
                "snow removal"],
    "subscriptions": ["netflix", "spotify", "disney", "apple", "google", "amazon", "crave", "youtube",
                      "fitness", "gym"],
}
CATEGORIES = [*CATEGORY_HINTS, "investment", "other"]

KNOWN_NAMES = {
    "td.com": "TD", "rbc.com": "RBC", "cibc.com": "CIBC", "scotiabank.com": "Scotiabank",
    "ctfs.com": "Triangle Mastercard", "americanexpress.com": "American Express",
    "rogersbank.com": "Rogers Bank", "bell.ca": "Bell", "hydroottawa.com": "Hydro Ottawa",
    "ottawa.ca": "City of Ottawa", "enbridge.com": "Enbridge Gas",
}

# Cadence
MONTHLY, EVERY_2_MONTHS, MAYBE_MONTHLY, PERIODIC, IRREGULAR, ONE_OFF = (
    "monthly", "every_2_months", "maybe_monthly", "periodic", "irregular", "one_off")
CADENCE_LABELS = {
    MONTHLY: "every month",
    EVERY_2_MONTHS: "every 2 months",
    MAYBE_MONTHLY: "2 months in a row",
    PERIODIC: "a few times a year",
    IRREGULAR: "irregular",
    ONE_OFF: "once",
}
RECURRING = (MONTHLY, EVERY_2_MONTHS, MAYBE_MONTHLY)

# What the latest email contains
AMOUNT, STATEMENT_NOTICE, NO_AMOUNT = "amount", "statement_notice", "no_amount"

FILTER_WORDS = ["bill", "statement", "invoice", "facture", "renewal"]

_GENERIC_BILLER = Biller(name="scan", senders=())


@dataclass
class SenderCandidate:
    email: str
    name: str
    category: str
    messages: list[tuple[datetime, str, str]] = field(default_factory=list)  # (received, id, subject)
    cadence: str = ONE_OFF
    kind: str = BILL
    content: str = ""  # AMOUNT / STATEMENT_NOTICE / NO_AMOUNT; "" when not checked
    sample_amount: Decimal | None = None
    duplicate_of: str = ""  # another sender from the same company that has the actual bill
    subject_filter: str = ""  # suggested subject_includes word
    already: bool = False  # already in bill_senders.toml

    @property
    def domain(self) -> str:
        return self.email.rsplit("@", 1)[-1]

    @property
    def base_domain(self) -> str:
        return ".".join(self.domain.split(".")[-2:])

    @property
    def count(self) -> int:
        return len(self.messages)

    @property
    def latest_at(self) -> datetime:
        return max(m[0] for m in self.messages)

    @property
    def latest_subject(self) -> str:
        return max(self.messages)[2]

    @property
    def subjects(self) -> list[str]:
        return [m[2] for m in self.messages]

    @property
    def months(self) -> set[str]:
        return {m[0].astimezone(settings.timezone).strftime("%Y-%m") for m in self.messages}

    @property
    def recurring(self) -> bool:
        return self.cadence in RECURRING

    @property
    def cadence_label(self) -> str:
        return f"{CADENCE_LABELS[self.cadence]} · {len(self.months)} mo"

    @property
    def kind_label(self) -> str:
        return KIND_LABELS[self.kind]

    @property
    def recommended(self) -> bool:
        return self.kind == BILL and self.content == AMOUNT and not self.duplicate_of

    @property
    def sort_key(self) -> tuple:
        cadence_rank = [MONTHLY, EVERY_2_MONTHS, MAYBE_MONTHLY, PERIODIC, IRREGULAR, ONE_OFF].index(self.cadence)
        content_rank = {AMOUNT: 0, STATEMENT_NOTICE: 1, NO_AMOUNT: 2}.get(self.content, 3)
        return bool(self.duplicate_of), cadence_rank, content_rank, -self.count, self.email


@dataclass
class DiscoveryResult:
    bills: list[SenderCandidate]  # service bills that arrive monthly / every 2 months
    periodic_bills: list[SenderCandidate]  # property tax, annual renewals
    card_statements: list[SenderCandidate]
    not_bills: list[SenderCandidate]  # investment statements, purchases, one-off card notices
    skipped: dict[str, int]


def discovery_query(days: int) -> str:
    subjects = " OR ".join(f'"{w}"' if " " in w else w for w in SUBJECT_WORDS)
    phrases = " OR ".join(f'"{p}"' for p in BODY_PHRASES)
    return f"newer_than:{days}d (subject:({subjects}) OR {phrases}) -category:promotions -category:social -from:me"


_cache: dict[int, tuple[float, "DiscoveryResult"]] = {}
_scan_lock = threading.Lock()  # a double-clicked Scan waits for the first one and gets its result


def discover_senders(days: int = DEFAULT_DAYS, refresh: bool = False) -> DiscoveryResult:
    with _scan_lock:
        cached = _cache.get(days)
        if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        result = _scan(days)
        _cache[days] = (time.monotonic(), result)
        return result


def _scan(days: int) -> DiscoveryResult:
    service = gmail_client.build_service(google_auth.get_credentials())
    ids = gmail_client.search_message_ids(service, discovery_query(days))[:MAX_MESSAGES]
    candidates, skipped = aggregate(_fetch_metadata(service, ids), own_email=google_auth.connected_account())
    classify(candidates)
    for c in candidates:
        if c.kind in (BILL, CARD) and (c.recurring or c.kind == BILL):
            _inspect_latest(service, c)
    mark_duplicates(candidates)
    return group(candidates, skipped)


def aggregate(messages: list[dict], own_email: str | None = None) -> tuple[list[SenderCandidate], dict[str, int]]:
    """Groups Gmail `format=metadata` messages by sender, dropping senders that are never bills."""
    by_sender: dict[str, SenderCandidate] = {}
    skipped = {"notifications": 0, "personal": 0}
    own_email = (own_email or "").lower()
    for msg in messages:
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        name, email = parseaddr(headers.get("from", ""))
        email = email.lower()
        subject = headers.get("subject", "")
        if "@" not in email:
            continue
        if IGNORED_SENDERS.search(email) or IGNORED_DISPLAY_NAMES.search(headers.get("from", "")):
            skipped["notifications"] += 1
            continue
        if (email == own_email or email.rsplit("@", 1)[-1] in PERSONAL_DOMAINS or is_person(name, email)
                or CONVERSATION_SUBJECT.search(subject)):
            skipped["personal"] += 1
            continue
        received = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, tz=timezone.utc)
        if email not in by_sender:
            domain = ".".join(email.rsplit("@", 1)[-1].split(".")[-2:])
            display = name if name and name.lower() != email else KNOWN_NAMES.get(domain, email)
            by_sender[email] = SenderCandidate(email, display, guess_category(display, email))
        by_sender[email].messages.append((received, msg["id"], subject))
    return list(by_sender.values()), skipped


def classify(candidates: list[SenderCandidate]) -> None:
    for c in candidates:
        c.cadence = classify_cadence(c.months)
        c.kind = classify_kind(c)
        if c.category == "other":
            c.category = guess_category(c.name, c.email, c.subjects)
        if c.kind == ACCOUNT:
            c.category = "investment"
        elif c.kind == CARD:
            c.category = "credit_card"
        c.subject_filter = suggest_subject_filter(c)


def classify_cadence(months: set[str]) -> str:
    """Typical gap between the months a sender writes in: 1 = monthly, 2 = every 2 months, 3-6 = periodic.
    The median tolerates a missed or extra email."""
    index = sorted(int(m[:4]) * 12 + int(m[5:]) for m in months)
    if len(index) == 1:
        return ONE_OFF
    gaps = [b - a for a, b in zip(index, index[1:])]
    typical = statistics.median(gaps)
    if len(index) == 2:
        return {1: MAYBE_MONTHLY, 2: EVERY_2_MONTHS}.get(gaps[0], PERIODIC if gaps[0] <= 6 else IRREGULAR)
    if typical <= 1:
        return MONTHLY
    if typical <= 2:
        return EVERY_2_MONTHS
    return PERIODIC if typical <= 6 else IRREGULAR


def classify_kind(c: SenderCandidate) -> str:
    text = " ".join([c.name, c.email, *c.subjects])
    if ACCOUNT_WORDS.search(text):
        return ACCOUNT
    if c.category == "credit_card" or CARD_WORDS.search(text):
        return CARD
    if PURCHASE_WORDS.search(" ".join(c.subjects)):
        return PURCHASE
    return BILL


def suggest_subject_filter(c: SenderCandidate) -> str:
    """A word every bill subject from this sender contains, so its other emails aren't taken for bills."""
    if c.kind == CARD:
        return "statement"
    subjects = [s.lower() for s in c.subjects]
    return next((w for w in FILTER_WORDS if all(w in s for s in subjects)), "")


def mark_duplicates(candidates: list[SenderCandidate]) -> None:
    """Hydro's 'Due date alert' sender duplicates Hydro's 'Ebill is ready' sender: same company, no amount."""
    with_amount = {c.base_domain: c for c in candidates if c.kind == BILL and c.content == AMOUNT}
    for c in candidates:
        primary = with_amount.get(c.base_domain)
        if c.kind == BILL and c.content != AMOUNT and primary is not None and primary is not c:
            c.duplicate_of = primary.name


def group(candidates: list[SenderCandidate], skipped: dict[str, int]) -> DiscoveryResult:
    candidates.sort(key=lambda c: c.sort_key)
    result = DiscoveryResult([], [], [], [], skipped)
    for c in candidates:
        if c.kind == BILL and c.recurring:
            result.bills.append(c)
        elif c.kind == BILL and c.cadence in (PERIODIC, IRREGULAR, ONE_OFF):
            result.periodic_bills.append(c)
        elif c.kind == CARD and c.recurring:
            result.card_statements.append(c)
        else:
            result.not_bills.append(c)
    return result


def inspect_content(text: str) -> tuple[str, Decimal | None]:
    parsed = parse_bill(text, _GENERIC_BILLER)
    if parsed.amount:  # $0.00 says nothing about the bill
        return AMOUNT, parsed.amount
    return (STATEMENT_NOTICE if STATEMENT_NOTICE_RE.search(text) else NO_AMOUNT), None


def guess_category(name: str, email: str, subjects: list[str] | None = None) -> str:
    """From the sender's name and address; falls back to subjects ("Water Utility Bill")."""
    for texts in ([name, email], [name, email, *(subjects or [])]):
        haystack = " ".join(texts).lower().replace("@", " ").replace(".", " ")
        for category, hints in CATEGORY_HINTS.items():
            for hint in hints:
                # whole word for short hints ("td", "rent"); word start otherwise ("utilit" -> "utility")
                pattern = rf"\b{re.escape(hint)}\b" if len(hint) <= 4 else rf"\b{re.escape(hint)}"
                if re.search(pattern, haystack):
                    return category
    return "other"


def _inspect_latest(service, candidate: SenderCandidate) -> None:
    """Reads up to the 2 newest emails (body + PDF text) and records what they contain."""
    for _, message_id, _ in sorted(candidate.messages, reverse=True)[:2]:
        msg = gmail_client.fetch_message(service, message_id)
        text = "\n".join([msg.subject, msg.text, *(pdf_text(a.data) for a in msg.attachments)])
        content, amount = inspect_content(text)
        if not candidate.content or content == AMOUNT:
            candidate.content, candidate.sample_amount = content, amount
        if content == AMOUNT:
            return


def _fetch_metadata(service, ids: list[str]) -> list[dict]:
    results: dict[str, dict] = {}
    failed: list[str] = []

    def collect(request_id, response, exception):
        if exception is None:
            results[request_id] = response
        else:
            failed.append(request_id)

    def get(message_id):
        return service.users().messages().get(
            userId="me", id=message_id, format="metadata", metadataHeaders=["From", "Subject"]
        )

    for start in range(0, len(ids), BATCH_SIZE):
        batch = service.new_batch_http_request(callback=collect)
        for message_id in ids[start : start + BATCH_SIZE]:
            batch.add(get(message_id), request_id=message_id)
        batch.execute()

    for message_id in failed:  # usually rate limiting inside a batch; retry one at a time
        results[message_id] = get(message_id).execute(num_retries=gmail_client.RETRIES)
    return list(results.values())
