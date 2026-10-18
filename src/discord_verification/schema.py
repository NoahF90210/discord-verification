"""Versioned SQLite challenge schema and atomic pre-release v1 upgrade."""

from __future__ import annotations

import sqlite3

CHALLENGES_SQL = """
    CREATE TABLE IF NOT EXISTS challenges (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        username TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
        email TEXT NOT NULL, email_key TEXT NOT NULL, challenge_id TEXT NOT NULL,
        digest TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0, ready INTEGER NOT NULL DEFAULT 0,
        eligible INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (guild_id, user_id)
    )
"""
RESERVED_EMAIL_SQL = """
    CREATE UNIQUE INDEX IF NOT EXISTS reserved_email
    ON challenges(guild_id,email_key) WHERE eligible=1
"""


def migrate_v1(db: sqlite3.Connection) -> None:
    """Preserve proof, challenges, fingerprints, and limits; roll back every DDL on failure."""
    db.execute("BEGIN IMMEDIATE")
    try:
        old_columns = {row[1] for row in db.execute("PRAGMA table_info(challenges)")}
        db.execute("ALTER TABLE challenges RENAME TO challenges_v1")
        db.execute(CHALLENGES_SQL)
        columns = (
            "guild_id,user_id,username,first_name,last_name,email,email_key,"
            "challenge_id,digest,created,expires,attempts,ready"
        )
        eligibility = "eligible" if "eligible" in old_columns else "1"
        # SQL fragments are fixed schema identifiers, never member-controlled data.
        db.execute(
            f"INSERT INTO challenges({columns},eligible) "
            f"SELECT {columns},{eligibility} FROM challenges_v1"
        )
        db.execute("DROP TABLE challenges_v1")
        request_columns = {row[1] for row in db.execute("PRAGMA table_info(requests)")}
        if "sent" not in request_columns:
            # Old requests were recorded only for actual send attempts, including failures.
            db.execute("ALTER TABLE requests ADD COLUMN sent INTEGER NOT NULL DEFAULT 1")
        member_columns = {row[1] for row in db.execute("PRAGMA table_info(members)")}
        if "revoked" not in member_columns:
            db.execute("ALTER TABLE members ADD COLUMN revoked INTEGER NOT NULL DEFAULT 0")
        db.execute(RESERVED_EMAIL_SQL)
        db.execute("PRAGMA user_version=2")
        db.commit()
    except BaseException:
        db.rollback()
        raise
