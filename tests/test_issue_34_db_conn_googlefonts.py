#!/usr/bin/env python3
"""Regression locks for issue #34 — DB-connection-string regex must not
match innocuous URLs like Google Fonts.

Before v2.100.2 the secret-scanner pattern was
``://[^:\\s]+:[^@\\s]+@[^\\s]+`` — every URL with a path-and-query
sequence that happens to contain a ``colon-then-at-sign`` matched.
Google Fonts CSS API URLs of the form
``https://fonts.googleapis.com/css2?family=Playfair+Display:wght@400;700&display=swap``
match this pattern incidentally because ``:wght@400`` looks like
``:password@host`` to a regex that doesn't know about scheme prefixes.

v2.100.2 anchors the regex to known DB scheme prefixes
(``postgres``, ``mysql``, ``mongodb``, ``redis``, etc.) so non-DB URLs
no longer collide. These tests pin:

1. Google Fonts URL → ZERO findings.
2. Real ``postgres://user:pass@host`` → STILL detected.
3. Real ``mongodb+srv://user:pass@cluster.x.mongodb.net`` → STILL detected.
4. Real ``mysql://root:secret@db.example.com:3306/main`` → STILL detected.
5. OCI image reference with `:tag@sha256:...` → ZERO findings.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _scan_for_db_conn(text: str) -> list[str]:
    """Return the matched substrings (if any) from the DB-conn pattern."""
    from cpv_validation_common import SECRET_PATTERNS

    db_patterns = [p for p, label in SECRET_PATTERNS if "Database Connection String" in label]
    assert db_patterns, "DB-conn pattern must exist in SECRET_PATTERNS"
    matches: list[str] = []
    for p in db_patterns:
        for m in p.finditer(text):
            matches.append(m.group(0))
    return matches


class TestNoFalsePositiveOnGoogleFonts:
    def test_google_fonts_link_no_match(self) -> None:
        """The exact issue #34 reproducer must produce zero matches."""
        text = (
            '<link href="https://fonts.googleapis.com/css2?'
            'family=Playfair+Display:wght@400;700&display=swap" rel="stylesheet">'
        )
        assert _scan_for_db_conn(text) == []

    def test_google_fonts_multiple_families_no_match(self) -> None:
        """Multiple `family=X:wght@N` segments — none should match."""
        text = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500&family=Playfair+Display:wght@400;700"
        assert _scan_for_db_conn(text) == []


class TestNoFalsePositiveOnOciImageRefs:
    def test_oci_image_with_tag_and_digest_no_match(self) -> None:
        """OCI image reference with `:tag@sha256:...` must not match."""
        text = "image: ghcr.io/example/foo:v1.2.3@sha256:abcdef1234567890"
        assert _scan_for_db_conn(text) == []


class TestStillCatchesRealDbConnections:
    def test_postgres_with_credentials_matches(self) -> None:
        text = "postgres://app_user:s3cr3t-pw@db.example.com:5432/main"
        matches = _scan_for_db_conn(text)
        assert matches, f"expected match for {text!r}; got nothing"

    def test_postgresql_with_credentials_matches(self) -> None:
        text = "postgresql://admin:p%40ssw0rd@db.internal:5432/orders"
        assert _scan_for_db_conn(text)

    def test_mongodb_srv_with_credentials_matches(self) -> None:
        text = "mongodb+srv://app:secretXYZ@cluster0.mongodb.net/myDb"
        assert _scan_for_db_conn(text)

    def test_mysql_with_credentials_matches(self) -> None:
        text = "DATABASE_URL=mysql://root:rootpw@localhost:3306/main"
        assert _scan_for_db_conn(text)

    def test_redis_with_credentials_matches(self) -> None:
        text = "REDIS_URL=redis://app:cachepass@redis.example.com:6379/0"
        assert _scan_for_db_conn(text)

    def test_amqps_with_credentials_matches(self) -> None:
        text = "amqps://producer:queuepass@rabbit.example.com:5671/vhost"
        assert _scan_for_db_conn(text)

    def test_clickhouse_with_credentials_matches(self) -> None:
        text = "clickhouse://default:clickpw@clickhouse.local:9000/analytics"
        assert _scan_for_db_conn(text)
