import base64
from datetime import datetime, timezone

from app.bills.senders import Biller, find_biller, gmail_query
from app.gmail_client import parse_message


def b64(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def test_parse_multipart_message_with_pdf_attachment():
    msg = {
        "id": "m1",
        "threadId": "t1",
        "snippet": "Your bill",
        "internalDate": "1791676800000",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Hydro Billing <EBills@Example-Hydro.com>"},
                {"name": "Subject", "value": "Your October bill"},
            ],
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {"data": b64("Amount due: $88.10")}},
                        {
                            "mimeType": "text/html",
                            "headers": [{"name": "Content-Type", "value": 'text/html; charset="iso-8859-1"'}],
                            "body": {"data": b64("<p>Amount&nbsp;due</p><td>$88.10</td>".encode("latin-1"))},
                        },
                    ],
                },
                {
                    "mimeType": "application/pdf",
                    "filename": "bill.pdf",
                    "body": {"attachmentId": "a1", "size": 4},
                },
                {"mimeType": "image/png", "filename": "logo.png", "body": {"attachmentId": "a2", "size": 4}},
                # a spoofed "PDF" that is really a web page: dropped
                {"mimeType": "text/html", "filename": "statement.pdf", "body": {"attachmentId": "a3", "size": 30}},
            ],
        },
    }
    loaded = []

    def loader(attachment_id):
        loaded.append(attachment_id)
        return b"%PDF-1.7 fake" if attachment_id == "a1" else b"<script>alert(1)</script>"

    fetched = parse_message(msg, loader)
    assert fetched.sender_email == "ebills@example-hydro.com"
    assert fetched.sender_name == "Hydro Billing"
    assert fetched.subject == "Your October bill"
    assert "Amount due: $88.10" in fetched.text
    assert "Amount due $88.10" in fetched.text  # HTML converted, nbsp normalised
    assert fetched.received_at == datetime.fromtimestamp(1791676800, tz=timezone.utc)
    assert [(a.filename, a.mime_type) for a in fetched.attachments] == [("bill.pdf", "application/pdf")]
    assert loaded == ["a1", "a3"]  # the PNG is never downloaded; the fake PDF is checked and dropped


def test_sender_matching_by_address_and_domain():
    billers = [
        Biller(name="Hydro", senders=("ebills@example-hydro.com",)),
        Biller(name="Insurer", senders=("@example-insurance.ca",)),
    ]
    assert find_biller(billers, "EBILLS@example-hydro.com").name == "Hydro"
    assert find_biller(billers, "notices@mail.example-insurance.ca").name == "Insurer"
    assert find_biller(billers, "someone@notexample-insurance.ca") is None
    assert find_biller(billers, "other@example-hydro.com") is None


def test_subject_filters():
    biller = Biller(name="Card", senders=("x@y.com",), subject_includes=("statement",), subject_excludes=("offer",))
    assert biller.accepts_subject("Your eStatement is ready")
    assert not biller.accepts_subject("A special offer on your statement")
    assert not biller.accepts_subject("Welcome!")


def test_gmail_query_uses_epoch_bounds():
    billers = [Biller(name="A", senders=("a@x.com", "@y.ca"))]
    start = datetime(2026, 10, 1, tzinfo=timezone.utc)
    end = datetime(2026, 11, 1, tzinfo=timezone.utc)
    assert gmail_query(billers, start, end) == (
        f"from:(a@x.com OR y.ca) after:{int(start.timestamp())} before:{int(end.timestamp())}"
    )
