"""Thin wrapper around the Gmail API: search, fetch, and decode bodies and PDF attachments."""

import base64
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parseaddr

from bs4 import BeautifulSoup
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024
# Gmail allows ~15,000 quota units per user per minute (a get costs 5). googleapiclient
# retries rate-limit (429 / 403 rateLimitExceeded) and 5xx errors with exponential backoff.
RETRIES = 6


@dataclass
class FetchedAttachment:
    filename: str
    mime_type: str
    data: bytes


@dataclass
class FetchedMessage:
    id: str
    thread_id: str
    sender_email: str
    sender_name: str
    subject: str
    received_at: datetime  # UTC
    snippet: str
    text: str  # plain-text body, with HTML converted to text
    attachments: list[FetchedAttachment] = field(default_factory=list)


def build_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def search_message_ids(service, query: str) -> list[str]:
    ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users().messages()
            .list(userId="me", q=query, maxResults=100, pageToken=page_token)
            .execute(num_retries=RETRIES)
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            return ids


def fetch_message(service, message_id: str) -> FetchedMessage:
    msg = service.users().messages().get(userId="me", id=message_id, format="full").execute(num_retries=RETRIES)

    def load_attachment(attachment_id: str) -> bytes:
        att = (
            service.users().messages().attachments()
            .get(userId="me", messageId=message_id, id=attachment_id)
            .execute(num_retries=RETRIES)
        )
        return _b64decode(att["data"])

    return parse_message(msg, load_attachment)


def parse_message(msg: dict, load_attachment: Callable[[str], bytes]) -> FetchedMessage:
    """Turns a Gmail API `format=full` message into a FetchedMessage. Pure apart from `load_attachment`."""
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}
    sender_name, sender_email = parseaddr(headers.get("from", ""))

    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[FetchedAttachment] = []

    for part in _walk(payload):
        mime = part.get("mimeType", "")
        body = part.get("body", {})
        filename = part.get("filename") or ""
        if filename:
            if _is_pdf(mime, filename) and body.get("size", 0) <= MAX_ATTACHMENT_BYTES:
                data = _b64decode(body["data"]) if body.get("data") else load_attachment(body["attachmentId"])
                attachments.append(FetchedAttachment(filename, mime, data))
            continue
        if not body.get("data"):
            continue
        if mime == "text/plain":
            text = _decode_text(body["data"], part)
            # some senders (Hydro Ottawa) put HTML in the plain-text part
            plain_parts.append(html_to_text(text) if re.search(r"<(p|br|td|div|table)\b", text, re.I) else text)
        elif mime == "text/html":
            html_parts.append(html_to_text(_decode_text(body["data"], part)))

    text = "\n\n".join(p for p in [*plain_parts, *html_parts] if p.strip())
    received_at = datetime.fromtimestamp(int(msg.get("internalDate", "0")) / 1000, tz=timezone.utc)

    return FetchedMessage(
        id=msg["id"],
        thread_id=msg.get("threadId", ""),
        sender_email=sender_email.lower(),
        sender_name=sender_name,
        subject=headers.get("subject", ""),
        received_at=received_at,
        snippet=msg.get("snippet", ""),
        text=normalize_whitespace(text),
        attachments=attachments,
    )


def friendly_error(exc: HttpError) -> str:
    if exc.resp.status in (403, 429) and "rate" in str(exc).lower():
        return "Gmail's per-minute limit was hit. Wait a minute and try again."
    return f"Gmail API error {exc.resp.status}: {exc.reason}"


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    return soup.get_text(" ")


def normalize_whitespace(text: str) -> str:
    text = text.replace(" ", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\s*\n\s*", "\n", text).strip()


def _walk(part: dict) -> Iterator[dict]:
    yield part
    for child in part.get("parts", []) or []:
        yield from _walk(child)


def _is_pdf(mime: str, filename: str) -> bool:
    return mime == "application/pdf" or filename.lower().endswith(".pdf")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _decode_text(data: str, part: dict) -> str:
    content_type = next(
        (h["value"] for h in part.get("headers", []) if h["name"].lower() == "content-type"), ""
    )
    match = re.search(r'charset="?([\w.-]+)"?', content_type, re.I)
    charset = match.group(1) if match else "utf-8"
    raw = _b64decode(data)
    try:
        # Many senders label UTF-8 text as Latin-1, which turns "©" into "Â©"
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode(charset, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")
