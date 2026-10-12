"""The scanner behind the pre-commit hook and security-check."""

from app import secret_scan


def test_generic_secrets_are_detected():
    # assembled at runtime so this file itself doesn't trip the pre-commit hook
    fake = [
        "token=access-" + "production-1a2b3c4d-5e6f-7a8b",
        "PLAID_PRODUCTION_" + "SECRETE=0123456789abcdef0123456789abcd",
        "-----BEGIN RSA " + "PRIVATE KEY-----",
        '{"client_secret": "GOCSPX-' + 'abcdefghijklmnop"}',
    ]
    found = secret_scan.scan_text("\n".join(fake), private=[])
    assert found == ["Plaid token", "Plaid secret", "private key", "Google client secret"]


def test_test_fixtures_and_docs_are_not_flagged():
    text = 'access-sandbox-secret  PLAID_PRODUCTION_SECRETE=prod-secret  "access-production-1234-abcd"'
    assert secret_scan.scan_text(text, private=[]) == []


def test_personal_details_match_ignoring_case_and_are_masked():
    found = secret_scan.scan_text("Hello JANE DOE, account 123456789", private=["jane doe", "123456789", "absent"])
    assert found == ["personal detail “ja••••••”", "personal detail “12•••••••”"]


def test_files_that_must_never_be_committed():
    for path in [".env", "secrete/client_secret_x.json", "data/cashflow.db", "app.key",
                 "config/private_identifiers.txt"]:
        assert secret_scan.FORBIDDEN_FILES.search(path), path
    for path in ["app/config.py", "config/bill_senders.example.toml", ".env.example.md"]:
        assert not secret_scan.FORBIDDEN_FILES.search(path), path
