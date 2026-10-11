import tomllib
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from app.bills.discover import (
    ACCOUNT, AMOUNT, BILL, CARD, EVERY_2_MONTHS, IRREGULAR, MAYBE_MONTHLY, MONTHLY, NO_AMOUNT, ONE_OFF,
    PERIODIC, PURCHASE, STATEMENT_NOTICE, aggregate, classify, classify_cadence, discovery_query, group,
    guess_category, inspect_content, mark_duplicates,
)
from app.bills.senders import Biller, BillerConfigError, add_billers, configured_senders, load_billers

EXAMPLE = Path(__file__).resolve().parent.parent / "config" / "bill_senders.example.toml"
_ids = iter(range(10_000))


def meta(sender: str, subject: str, month: int, year: int = 2026) -> dict:
    when = datetime(year, month, 15, 16, tzinfo=timezone.utc)
    return {
        "id": str(next(_ids)),
        "internalDate": str(int(when.timestamp() * 1000)),
        "payload": {"headers": [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}]},
    }


def monthly(sender: str, subject: str, months) -> list[dict]:
    return [meta(sender, subject, m) for m in months]


INBOX = [
    *monthly("Hydro Ottawa <myaccount@hydroottawa.com>", "Your Ebill is Ready", range(4, 10)),
    *monthly("noreply@hydroottawa.com", "Due date alert", range(4, 9)),
    *monthly("Bell <ebill@bell.ca>", "Your Bell e-Bill is ready", range(4, 10)),
    *monthly("City of Ottawa / Ville d'Ottawa <wb-fe@ottawa.ca>", "Water Utility Bill 1 MAIN ST", [5, 7, 9]),
    *monthly("City of Ottawa <pt-if@ottawa.ca>", "City of Ottawa - Property Tax Bill", [2, 5]),
    *monthly("statements@bank.ctfs.com", "Your Triangle World Elite Mastercard eStatement is Ready", range(6, 10)),
    *monthly("Rogers Bank <rogers@e.rogersbank.com>", "Your statement is available", [5, 6]),
    *monthly("TD <td.wealth@td.com>", "Your TD Wealth Financial Planning account statement is available", range(4, 10)),
    meta("Hertz <hertznoreply@rentals.hertz.com>", "Hertz Receipt", 9),
    meta("Hertz <reservations@emails.hertz.com>", "My Hertz Reservation", 7),
    meta("Kodiak Snow Removal <info@kodiaksnowremoval.ca>", "Your 2026-27 snow removal renewal is ready", 9),
    # never bills
    *monthly("Me <me@gmail.com>", "Fwd: My Hertz Reservation", range(5, 9)),
    meta("Friend <friend@gmail.com>", "Tax Information", 4),
    meta("Colleague <colleague@work.example>", "RE: Request for Snow Removal Renewal", 9),
    meta("Jane Doe (via Google Drive) <drive-shares-dm-noreply@google.com>", "bank statement.pdf", 9),
    meta("Jane Doe（通过Google 云端硬盘发送） <drive-shares-noreply@google.com>", "bill", 9),
]


def scan(inbox, amounts):
    """Runs everything _scan does except Gmail calls; `amounts` stands in for _inspect_latest."""
    candidates, skipped = aggregate(inbox, own_email="me@gmail.com")
    classify(candidates)
    for c in candidates:
        c.content, c.sample_amount = amounts.get(c.email, (NO_AMOUNT, None))
    mark_duplicates(candidates)
    return group(candidates, skipped)


def test_scan_groups_real_world_inbox():
    result = scan(INBOX, {
        "myaccount@hydroottawa.com": (AMOUNT, Decimal("121.83")),
        "ebill@bell.ca": (AMOUNT, Decimal("55.50")),
        "wb-fe@ottawa.ca": (AMOUNT, Decimal("153.78")),
        "pt-if@ottawa.ca": (AMOUNT, Decimal("2100.00")),
        "statements@bank.ctfs.com": (AMOUNT, Decimal("92.41")),
        "rogers@e.rogersbank.com": (STATEMENT_NOTICE, None),
    })
    emails = lambda cs: [c.email for c in cs]  # noqa: E731

    assert emails(result.bills) == [
        "ebill@bell.ca", "myaccount@hydroottawa.com", "wb-fe@ottawa.ca", "noreply@hydroottawa.com",
    ]
    bell, hydro, water, hydro_alerts = result.bills
    assert [c.recommended for c in result.bills] == [True, True, True, False]
    assert hydro_alerts.duplicate_of == "Hydro Ottawa"
    assert water.cadence == EVERY_2_MONTHS and water.category == "utilities"
    assert (bell.category, bell.subject_filter) == ("phone_internet", "bill")

    assert emails(result.periodic_bills) == ["pt-if@ottawa.ca", "info@kodiaksnowremoval.ca"]
    assert result.periodic_bills[0].category == "housing"

    assert emails(result.card_statements) == ["statements@bank.ctfs.com", "rogers@e.rogersbank.com"]
    assert all(c.kind == CARD and c.category == "credit_card" and c.subject_filter == "statement"
               for c in result.card_statements)
    assert result.card_statements[1].name == "Rogers Bank"

    kinds = {c.email: c.kind for c in result.not_bills}
    assert kinds == {
        "td.wealth@td.com": ACCOUNT,
        "hertznoreply@rentals.hertz.com": PURCHASE,
        "reservations@emails.hertz.com": PURCHASE,
    }
    assert result.skipped == {"notifications": 2, "personal": 6}


def test_cadence():
    assert classify_cadence({"2026-05", "2026-06", "2026-07", "2026-08", "2026-09"}) == MONTHLY
    assert classify_cadence({"2026-05", "2026-06", "2026-08", "2026-09"}) == MONTHLY  # one missed email
    assert classify_cadence({"2026-05", "2026-07", "2026-09"}) == EVERY_2_MONTHS  # Ottawa water
    assert classify_cadence({"2026-09", "2026-10"}) == MAYBE_MONTHLY
    assert classify_cadence({"2026-02", "2026-05"}) == PERIODIC  # property tax
    assert classify_cadence({"2025-10", "2026-10"}) == IRREGULAR
    assert classify_cadence({"2026-08"}) == ONE_OFF


def test_inspect_content():
    assert inspect_content("Amount due: $120.50") == (AMOUNT, Decimal("120.50"))
    assert inspect_content("Your eStatement is ready. Balance $0.00") == (STATEMENT_NOTICE, None)
    assert inspect_content("Your TD eStatement is now available") == (STATEMENT_NOTICE, None)
    assert inspect_content("Thanks for being a customer") == (NO_AMOUNT, None)


def test_guess_category():
    assert guess_category("TD Canada Trust", "noreply@td.com") == "credit_card"
    assert guess_category("Rogers Bank", "rogers@e.rogersbank.com") == "credit_card"
    assert guess_category("Rogers", "billing@rogers.com") == "phone_internet"
    assert guess_category("Intact Insurance", "no-reply@intact.ca") == "insurance"
    assert guess_category("City of Ottawa", "wb-fe@ottawa.ca", ["Water Utility Bill"]) == "utilities"
    assert guess_category("Mailchimp", "news@stdomain.com", ["Your current plan"]) == "other"


def test_discovery_query_excludes_promotions_and_own_mail():
    q = discovery_query(90)
    assert q.startswith("newer_than:90d (subject:(")
    assert "-category:promotions" in q and "-from:me" in q


def test_example_senders_are_rejected(tmp_path):
    path = tmp_path / "bill_senders.toml"
    path.write_text(EXAMPLE.read_text())
    with pytest.raises(BillerConfigError, match="example senders"):
        load_billers(path)


def test_add_billers_replaces_examples_then_appends(tmp_path):
    path = tmp_path / "bill_senders.toml"
    path.write_text(EXAMPLE.read_text())

    add_billers(path, [Biller(name='Hydro "Ottawa"', senders=("ebill@hydroottawa.com",), category="utilities",
                              subject_includes=("bill",))], EXAMPLE)
    text = path.read_text()
    assert text.startswith("# Bill senders")
    assert "example-hydro" not in text.split("[[biller]]", 1)[1]
    [hydro] = load_billers(path)
    assert (hydro.name, hydro.subject_includes) == ('Hydro "Ottawa"', ("bill",))

    add_billers(path, [
        Biller(name="Rogers", senders=("@rogers.com",), category="phone_internet"),
        Biller(name="Dup", senders=("ebill@hydroottawa.com",)),  # already there: skipped
    ], EXAMPLE)
    billers = load_billers(path)
    assert [(b.name, b.senders) for b in billers] == [
        ('Hydro "Ottawa"', ("ebill@hydroottawa.com",)),
        ("Rogers", ("@rogers.com",)),
    ]
    assert configured_senders(path) == {"ebill@hydroottawa.com", "@rogers.com"}
    tomllib.loads(path.read_text())  # still valid TOML


def test_people_are_not_billers():
    from app.bills.discover import is_person

    assert is_person("Jane Doe-Smith", "jane_doe-smith@company.example")
    assert not is_person("Kodiak Snow Removal", "info@kodiaksnowremoval.ca")
    assert not is_person("Hydro Ottawa", "myaccount@hydroottawa.com")
    assert guess_category("Hertz", "hertznoreply@rentals.hertz.com", ["Hertz Receipt"]) == "other"
