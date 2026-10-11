"""Requests from other websites, foreign Host headers, and attachment handling."""

import dataclasses
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import db, google_auth, main
from app.bills import service
from app.config import settings
from app.main import app
from app.models import Attachment, BillEmail


@pytest.fixture
def client(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    db.Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(main, "settings", dataclasses.replace(settings, data_dir=tmp_path))
    calls = []
    monkeypatch.setattr(google_auth, "disconnect", lambda: calls.append("disconnect"))
    monkeypatch.setattr(google_auth, "connected_account", lambda: None)
    c = TestClient(app, base_url="http://localhost")
    c.calls = calls
    c.tmp = tmp_path
    return c


def test_foreign_host_header_is_rejected(client):
    """DNS rebinding: another site's domain pointed at 127.0.0.1."""
    assert client.get("/api/settings", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/settings", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/api/settings", headers={"Host": "127.0.0.1:8000"}).status_code == 200


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example", "Content-Type": "text/plain"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Sec-Fetch-Site": "same-site", "Origin": "http://localhost:3000"},
])
def test_cross_site_requests_are_blocked(client, headers):
    r = client.post("/api/gmail/disconnect", headers=headers)
    assert r.status_code == 403
    assert client.calls == []  # the handler never ran


def test_requests_from_the_app_itself_still_work(client):
    for headers in [
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:8000"},  # the built app
        {"Sec-Fetch-Site": "same-origin", "Origin": "http://localhost:5173"},  # vite dev server proxy
        {},  # curl / CLI
    ]:
        assert client.post("/api/gmail/disconnect", headers=headers).status_code == 200
    assert client.calls == ["disconnect"] * 3


def test_cross_site_reads_of_data_are_blocked_but_google_can_redirect_back(client, monkeypatch):
    assert client.get("/api/dashboard", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/attachments/1", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    # Google's redirect after sign-in is a cross-site navigation and must still get through
    monkeypatch.setattr(google_auth, "complete_authorization", lambda state, code: "me@example.com")
    r = client.get("/auth/google/callback?state=s&code=c", headers={"Sec-Fetch-Site": "cross-site"},
                   follow_redirects=False)
    assert r.status_code == 303 and "Connected" in r.headers["location"]


def test_security_headers(client):
    r = client.get("/api/settings")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "default-src 'self'" in r.headers["content-security-policy"]


def _attachment(client, data: bytes, mime: str) -> int:
    (client.tmp / "attachments").mkdir(exist_ok=True)
    (client.tmp / "attachments" / "a.pdf").write_bytes(data)
    with db.session_scope() as s:
        email = BillEmail(gmail_message_id="m", thread_id="t", sender_email="x@y.z", subject="s",
                          received_at=datetime(2026, 10, 1))
        s.add(email)
        s.flush()
        att = Attachment(email_id=email.id, filename="bill.pdf", mime_type=mime, path="attachments/a.pdf",
                         size=len(data))
        s.add(att)
        s.flush()
        return att.id


def test_attachments_are_always_served_as_pdf(client):
    att_id = _attachment(client, b"%PDF-1.7 real enough", mime="text/html")  # sender lied about the type
    r = client.get(f"/attachments/{att_id}")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_non_pdf_attachments_are_not_served(client):
    att_id = _attachment(client, b"<script>alert(document.cookie)</script>", mime="application/pdf")
    assert client.get(f"/attachments/{att_id}").status_code == 404
