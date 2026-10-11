"""Loads config/bill_senders.toml: which sender addresses are bills, and how to read them."""

import json
import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class Biller:
    name: str
    senders: tuple[str, ...]  # full addresses, or "@domain.com" for a whole domain
    category: str = "other"
    currency: str = "CAD"
    subject_includes: tuple[str, ...] = ()
    subject_excludes: tuple[str, ...] = ()
    amount_patterns: tuple[str, ...] = ()
    due_date_patterns: tuple[str, ...] = ()
    dayfirst: bool = False  # for numeric dates like 05/10/2026

    def matches_sender(self, email: str) -> bool:
        email = email.lower()
        for sender in self.senders:
            if sender.startswith("@"):
                domain = sender[1:]
                if email.endswith("@" + domain) or email.endswith("." + domain):
                    return True
            elif email == sender:
                return True
        return False

    def accepts_subject(self, subject: str) -> bool:
        subject = subject.lower()
        if any(word in subject for word in self.subject_excludes):
            return False
        return not self.subject_includes or any(word in subject for word in self.subject_includes)


class BillerConfigError(Exception):
    pass


# Placeholder senders shipped in bill_senders.example.toml
EXAMPLE_SENDERS = {"ebills@example-hydro.com", "@example-insurance.ca", "statements@example-bank.com"}

NO_SENDERS_HINT = "Click “Find bill senders” to pick your real bill senders, or edit config/bill_senders.toml."


def load_billers(path: Path) -> list[Biller]:
    if not path.exists():
        raise BillerConfigError(f"No bill senders configured yet. {NO_SENDERS_HINT}")
    with path.open("rb") as f:
        data = tomllib.load(f)

    if any(s.lower() in EXAMPLE_SENDERS for entry in data.get("biller", []) for s in entry.get("senders", [])):
        raise BillerConfigError(f"bill_senders.toml still has the example senders. {NO_SENDERS_HINT}")

    billers = []
    for entry in data.get("biller", []):
        if not entry.get("name") or not entry.get("senders"):
            raise BillerConfigError(f"Each [[biller]] needs a name and senders: {entry}")
        billers.append(
            Biller(
                name=entry["name"],
                senders=tuple(s.strip().lower() for s in entry["senders"]),
                category=entry.get("category", "other"),
                currency=entry.get("currency", "CAD"),
                subject_includes=tuple(w.lower() for w in entry.get("subject_includes", [])),
                subject_excludes=tuple(w.lower() for w in entry.get("subject_excludes", [])),
                amount_patterns=tuple(entry.get("amount_patterns", [])),
                due_date_patterns=tuple(entry.get("due_date_patterns", [])),
                dayfirst=entry.get("dayfirst", False),
            )
        )
    if not billers:
        raise BillerConfigError(f"No bill senders configured yet. {NO_SENDERS_HINT}")
    return billers


def configured_senders(path: Path) -> set[str]:
    """Every sender in the file, examples included. Never raises."""
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    return {s.lower() for entry in data.get("biller", []) for s in entry.get("senders", [])}


def add_billers(path: Path, new: list[Biller], header_from: Path) -> None:
    """Appends billers to the TOML file, keeping whatever is already there.

    If the file is missing or still holds the example senders, it's rewritten as
    the example's comment header plus the new entries.
    """
    existing = configured_senders(path)
    if not path.exists() or existing & EXAMPLE_SENDERS:
        header = []
        for line in header_from.read_text().splitlines():
            if line.strip() and not line.startswith("#"):
                break
            header.append(line)
        content = "\n".join(header).rstrip() + "\n"
        existing = set()
    else:
        content = path.read_text().rstrip() + "\n"

    for biller in new:
        senders = [s for s in biller.senders if s not in existing]
        if not senders:
            continue
        existing.update(senders)
        content += (
            f"\n[[biller]]\n"
            f"name = {_toml_string(biller.name)}\n"
            f"senders = [{', '.join(_toml_string(s) for s in senders)}]\n"
            f"category = {_toml_string(biller.category)}\n"
        )
        if biller.subject_includes:
            content += f"subject_includes = [{', '.join(_toml_string(w) for w in biller.subject_includes)}]\n"
    path.write_text(content)


def _toml_string(value: str) -> str:
    # JSON string escapes are valid TOML basic-string escapes
    return json.dumps(value, ensure_ascii=False)


def find_biller(billers: list[Biller], sender_email: str) -> Biller | None:
    return next((b for b in billers if b.matches_sender(sender_email)), None)


def gmail_query(billers: list[Biller], start: datetime, end: datetime) -> str:
    """Gmail search for every configured sender within [start, end).

    Epoch seconds are used because Gmail reads YYYY/MM/DD dates as midnight Pacific time.
    """
    senders = sorted({s.lstrip("@") for b in billers for s in b.senders})
    return f"from:({' OR '.join(senders)}) after:{int(start.timestamp())} before:{int(end.timestamp())}"
