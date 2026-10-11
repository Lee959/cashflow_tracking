"""Pulls the amount and due date out of a bill email's text.

Order of attempts:
  1. the biller's own regexes from bill_senders.toml (most reliable; add these as you learn each format)
  2. generic "keyword followed by a $ amount / date" rules below
  3. if the email contains exactly one dollar amount, use it
"eStatement is ready" emails with no amount are flagged as statement notices; anything
else still missing is left for review in the UI.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as dateparser

from app.bills.senders import Biller

# Most specific first: the first keyword that has an amount after it wins.
AMOUNT_KEYWORDS = [
    "total amount due",
    "amount due",
    "total due",
    "balance due",
    "amount owing",
    "total balance owing",
    "amount to pay",
    "payment amount",
    "new balance",
    "statement balance",
    "total amount",
    "current charges",
    "minimum payment",
    "montant dû",
    "montant à payer",
    "solde à payer",
]

DUE_DATE_KEYWORDS = [
    "payment due date",
    "due date",
    "payment due",
    "due on",
    "due by",
    "pay by",
    "please pay by",
    "date d'échéance",
    # pre-authorized debits: the date the money actually leaves the account
    "withdrawal date",
    "payment date",
    "debit date",
]

# Amounts must have cents, which keeps account numbers, years and phone numbers out.
# A minus touching the number or the $ ("$-5.03", "-$5.03") or a trailing "CR" marks a credit.
_EN_AMOUNT = r"(?:(?P<neg>-\$|\$-|-)|\$\s?)?(?P<en>\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})(?P<cr>\s?CR\b)?"
_FR_AMOUNT = r"(?P<fr>\d{1,3}(?:[ \u00a0\u202f]\d{3})*,\d{2})\s?\$"
AMOUNT_RE = re.compile(f"{_EN_AMOUNT}|{_FR_AMOUNT}")

_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?"
DATE_RE = re.compile(
    rf"({_MONTH}\s+\d{{1,2}},?\s+\d{{4}}"  # October 15, 2026
    rf"|\d{{1,2}}\s+{_MONTH},?\s+\d{{4}}"  # 15 Oct 2026
    r"|\d{4}[-/. ]\d{1,2}[-/. ]\d{1,2}"  # 2026-10-15, 2026/10/15, 2026 10 15 (Bell)
    r"|\d{1,2}/\d{1,2}/\d{4})",  # 10/15/2026 or 15/10/2026 (see Biller.dayfirst)
    re.I,
)

# How far after a keyword to look for its value.
WINDOW = 80

# "Your eStatement is ready" emails: a real monthly statement, but the amount is only in online banking.
STATEMENT_NOTICE_RE = re.compile(
    r"e-?statement|statement (?:is )?(?:now )?(?:ready|available)|new statement|view your statement|relevé",
    re.I,
)


@dataclass
class ParseResult:
    amount: Decimal | None = None
    due_date: date | None = None
    statement_notice: bool = False  # no amount, but it's a "statement is ready" email
    notes: list[str] = field(default_factory=list)


def parse_bill(text: str, biller: Biller) -> ParseResult:
    result = ParseResult()
    result.amount = _amount_from_patterns(text, biller.amount_patterns, result.notes)
    if result.amount is None:
        result.amount = _amount_after_keyword(text, result.notes)
    if result.amount is None:
        result.amount = _single_amount(text, result.notes)

    result.due_date = _date_from_patterns(text, biller.due_date_patterns, biller.dayfirst, result.notes)
    if result.due_date is None:
        result.due_date = _date_after_keyword(text, biller.dayfirst, result.notes)

    if result.amount is None:
        result.statement_notice = bool(STATEMENT_NOTICE_RE.search(text))
        result.notes.append("statement notice: amount is in online banking" if result.statement_notice
                            else "no amount found")
    return result


def to_decimal(raw: str) -> Decimal | None:
    raw = raw.replace("$", "").replace("\u202f", " ").replace("\u00a0", " ").strip()
    if re.fullmatch(r"\d{1,3}(?:[ \u00a0]\d{3})*,\d{2}", raw):  # French: 1 234,56
        raw = raw.replace(" ", "").replace(",", ".")
    else:
        raw = raw.replace(",", "").replace(" ", "")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def amount_from_match(m: re.Match) -> Decimal | None:
    if m.group("fr"):
        return to_decimal(m.group("fr"))
    value = to_decimal(m.group("en"))
    if value is not None and (m.group("neg") or m.group("cr")):
        value = -value
    return value


def to_date(raw: str, dayfirst: bool = False) -> date | None:
    if m := re.fullmatch(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", raw.strip()):
        raw = "-".join(m.groups())
    try:
        return dateparser.parse(raw, dayfirst=dayfirst).date()
    except (ValueError, OverflowError):
        return None


def _amount_from_patterns(text: str, patterns: tuple[str, ...], notes: list[str]) -> Decimal | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match and (value := to_decimal(match.group(1))) is not None:
            notes.append(f"amount from biller pattern {pattern!r}")
            return value
    return None


def _amount_after_keyword(text: str, notes: list[str]) -> Decimal | None:
    lower = text.lower()
    for keyword in AMOUNT_KEYWORDS:
        for kw_match in re.finditer(re.escape(keyword), lower):
            window = text[kw_match.end() : kw_match.end() + WINDOW]
            amount_match = AMOUNT_RE.search(window)
            if amount_match:
                value = amount_from_match(amount_match)
                if value is not None:
                    notes.append(f"amount after '{keyword}'")
                    return value
    return None


def _single_amount(text: str, notes: list[str]) -> Decimal | None:
    values = {amount_from_match(m) for m in AMOUNT_RE.finditer(text)}
    values.discard(None)
    if len(values) == 1:
        notes.append("only dollar amount in the email (check it)")
        return values.pop()
    return None


def _date_from_patterns(text: str, patterns: tuple[str, ...], dayfirst: bool, notes: list[str]) -> date | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match and (value := to_date(match.group(1), dayfirst)) is not None:
            notes.append(f"due date from biller pattern {pattern!r}")
            return value
    return None


def _date_after_keyword(text: str, dayfirst: bool, notes: list[str]) -> date | None:
    lower = text.lower()
    for keyword in DUE_DATE_KEYWORDS:
        for kw_match in re.finditer(re.escape(keyword), lower):
            window = text[kw_match.end() : kw_match.end() + WINDOW]
            date_match = DATE_RE.search(window)
            if date_match and (value := to_date(date_match.group(1), dayfirst)) is not None:
                notes.append(f"due date after '{keyword}'")
                return value
    return None
