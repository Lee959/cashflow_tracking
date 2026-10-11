from datetime import date
from decimal import Decimal

from app.bills.parser import parse_bill, to_decimal
from app.bills.senders import Biller

BILLER = Biller(name="Test", senders=("bills@example.com",))


def test_amount_and_due_date_after_keywords():
    text = "Your bill is ready.\nAmount due: $1,234.56\nDue date: October 25, 2026\nAccount 123456789"
    result = parse_bill(text, BILLER)
    assert result.amount == Decimal("1234.56")
    assert result.due_date == date(2026, 10, 25)


def test_more_specific_keyword_wins_over_minimum_payment():
    text = "Minimum payment $10.00 New balance $845.20 Payment due date Nov 3, 2026"
    result = parse_bill(text, BILLER)
    assert result.amount == Decimal("845.20")
    assert result.due_date == date(2026, 11, 3)


def test_html_table_style_text():
    text = "Total Amount Due $ 98.07 Please pay by 2026-10-30"
    result = parse_bill(text, BILLER)
    assert result.amount == Decimal("98.07")
    assert result.due_date == date(2026, 10, 30)


def test_french_amount():
    text = "Montant dû : 1 234,56 $"
    assert parse_bill(text, BILLER).amount == Decimal("1234.56")


def test_single_amount_fallback():
    result = parse_bill("Thanks! We received $45.00 for your account.", BILLER)
    assert result.amount == Decimal("45.00")
    assert any("only dollar amount" in n for n in result.notes)


def test_estatement_notice_without_amount():
    result = parse_bill("Your eStatement is ready. Sign in to view it. Account ending 4421.", BILLER)
    assert result.amount is None
    assert result.statement_notice


def test_no_amount_needs_review():
    result = parse_bill("Your policy documents are attached. Account ending 4421.", BILLER)
    assert result.amount is None
    assert not result.statement_notice
    assert "no amount found" in result.notes


def test_biller_patterns_take_priority():
    biller = Biller(
        name="Insurer",
        senders=("@ins.ca",),
        amount_patterns=(r"Premium:\s*\$([\d,]+\.\d{2})",),
        due_date_patterns=(r"Withdrawal on (\d{2}/\d{2}/\d{4})",),
        dayfirst=True,
    )
    text = "Amount due $1.00 Premium: $210.40 Withdrawal on 05/11/2026"
    result = parse_bill(text, biller)
    assert result.amount == Decimal("210.40")
    assert result.due_date == date(2026, 11, 5)


def test_years_and_account_numbers_are_not_amounts():
    assert parse_bill("Statement for 2026, account 5500123", BILLER).amount is None


def test_to_decimal():
    assert to_decimal("$1,000.00") == Decimal("1000.00")
    assert to_decimal("12 345,67") == Decimal("12345.67")


def test_real_world_formats():
    # Hydro Ottawa: a credit, and a pre-authorized withdrawal date instead of a due date
    credit = parse_bill("Amount Due : $-5.03 Due Date : 2026-09-25", BILLER)
    assert (credit.amount, credit.due_date) == (Decimal("-5.03"), date(2026, 9, 25))
    hydro = parse_bill("Amount Due : $121.83 Automated Payment Withdrawal Date : 2026-10-19", BILLER)
    assert (hydro.amount, hydro.due_date) == (Decimal("121.83"), date(2026, 10, 19))
    # Bell: space-separated date
    bell = parse_bill("Account: 123456789 Payment date: 2026 10 09 Amount due: $55.50", BILLER)
    assert (bell.amount, bell.due_date) == (Decimal("55.50"), date(2026, 10, 9))
    # Ottawa water: no $ sign
    water = parse_bill("Amount Due: 153.78 Due Date: 2026-10-08", BILLER)
    assert (water.amount, water.due_date) == (Decimal("153.78"), date(2026, 10, 8))


def test_dash_before_amount_is_not_a_credit():
    assert parse_bill("Amount due - $45.00", BILLER).amount == Decimal("45.00")
    assert parse_bill("New balance $310.20 CR", BILLER).amount == Decimal("-310.20")
