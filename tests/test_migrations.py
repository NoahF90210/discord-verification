import hashlib
import hmac
import sqlite3
from contextlib import closing

import pytest
from conftest import request

from discord_verification.errors import VerificationError
from discord_verification.schema import migrate_v1
from discord_verification.storage import Storage
from discord_verification.verification import VerificationService

# Exact original v1 table layout: unconditional pending-email uniqueness, no sent/eligible.
V1_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE members (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    username TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
    email TEXT NOT NULL, email_key TEXT NOT NULL, verified_at REAL NOT NULL,
    role_granted INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(guild_id,user_id), UNIQUE(guild_id,email_key)
);
CREATE TABLE challenges (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    username TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
    email TEXT NOT NULL, email_key TEXT NOT NULL, challenge_id TEXT NOT NULL,
    digest TEXT NOT NULL, created REAL NOT NULL, expires REAL NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, ready INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(guild_id,user_id), UNIQUE(guild_id,email_key)
);
CREATE TABLE requests (
    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    email_key TEXT NOT NULL, created REAL NOT NULL
);
PRAGMA user_version=1;
"""
SECRET = b"test-secret-" * 4


def mac(text):
    return hmac.new(SECRET, text.encode(), hashlib.sha256).hexdigest()


@pytest.fixture
def legacy_path(tmp_path, clock):
    path = tmp_path / "legacy.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.executescript(V1_SCHEMA)
        db.execute(
            "INSERT INTO metadata VALUES ('secret_fingerprint', ?)",
            (mac("database-secret-fingerprint"),),
        )
        db.execute(
            "INSERT INTO members VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                900,
                "synthetic",
                "Existing",
                "Member",
                "existing@ucsb.edu",
                mac("email|1|existing@ucsb.edu"),
                clock.now - 10,
                1,
                0,
            ),
        )
        db.execute(
            "INSERT INTO challenges VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                1,
                10,
                "synthetic",
                "Pending",
                "Member",
                "pending@ucsb.edu",
                mac("email|1|pending@ucsb.edu"),
                "pending-id",
                mac("code|1|10|pending-id|123456"),
                clock.now - 10,
                clock.now + 590,
                2,
                1,
            ),
        )
        db.execute(
            "INSERT INTO requests VALUES (?,?,?,?)",
            (
                1,
                10,
                mac("email|1|pending@ucsb.edu"),
                clock.now - 10,
            ),
        )
        db.commit()
    return path


async def test_original_v1_upgrade_preserves_proof_codes_attempts_and_limits(
    legacy_path,
    mailer,
    clock,
):
    storage = Storage(legacy_path)
    service = VerificationService(storage, mailer, SECRET, clock=clock)
    existing = await service.member(1, 900)
    assert existing.email == "existing@ucsb.edu" and existing.role_granted and not existing.revoked
    with closing(sqlite3.connect(legacy_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT eligible,attempts FROM challenges").fetchone() == (1, 2)
        assert db.execute("SELECT sent FROM requests").fetchone()[0] == 1
    with pytest.raises(VerificationError, match="wait"):
        await request(service, email="other@ucsb.edu")
    assert (await service.verify(1, 10, "123456")).first_name == "Pending"
    await request(service, user=11, email="pending@umail.ucsb.edu")
    await request(service, user=12, email="pending@ucsb.edu")
    assert mailer.messages == []
    with closing(sqlite3.connect(legacy_path)) as db:
        assert db.execute("SELECT eligible FROM challenges").fetchall() == [(0,), (0,)]
    reopened = VerificationService(Storage(legacy_path), mailer, SECRET, clock=clock)
    assert (await reopened.member(1, 900)).role_granted


async def test_recent_v1_layout_preserves_decoys_and_unsent_counters(
    storage, service, mailer, clock
):
    await request(service, user=900)
    await service.verify(1, 900, mailer.messages[-1][1])
    await request(service, user=11)
    await request(service, user=12, email="member@umail.ucsb.edu")
    with closing(sqlite3.connect(storage.path)) as db:
        db.execute("PRAGMA user_version=1")
        before = db.execute(
            "SELECT user_id,eligible,attempts FROM challenges ORDER BY user_id"
        ).fetchall()
        counters = db.execute("SELECT user_id,sent FROM requests ORDER BY user_id").fetchall()
    upgraded = Storage(storage.path)
    with closing(sqlite3.connect(upgraded.path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            db.execute(
                "SELECT user_id,eligible,attempts FROM challenges ORDER BY user_id"
            ).fetchall()
            == before
        )
        assert (
            db.execute("SELECT user_id,sent FROM requests ORDER BY user_id").fetchall() == counters
        )
    restarted = VerificationService(upgraded, mailer, SECRET, clock=clock)
    with pytest.raises(VerificationError, match="Incorrect"):
        await restarted.verify(1, 11, mailer.messages[0][1])


def test_migration_failure_rolls_back_ddl_data_and_version(legacy_path):
    with closing(sqlite3.connect(legacy_path, isolation_level=None)) as db:
        before = db.execute("SELECT * FROM challenges").fetchall()

        def reject_final_version(action, arg1, arg2, database, trigger):
            if action == sqlite3.SQLITE_PRAGMA and arg1 == "user_version" and arg2 == "2":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(reject_final_version)
        with pytest.raises(sqlite3.DatabaseError):
            migrate_v1(db)
        db.set_authorizer(None)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert "eligible" not in {row[1] for row in db.execute("PRAGMA table_info(challenges)")}
        assert "sent" not in {row[1] for row in db.execute("PRAGMA table_info(requests)")}
        assert db.execute("SELECT * FROM challenges").fetchall() == before
        assert db.execute("SELECT count(*) FROM members").fetchone()[0] == 1
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='challenges_v1'").fetchone()
    # A clean restart can retry the upgrade after the injected failure.
    Storage(legacy_path)
    with closing(sqlite3.connect(legacy_path)) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2


def test_early_v1_without_revocation_column_gets_safe_default(legacy_path):
    with closing(sqlite3.connect(legacy_path)) as db:
        db.execute("ALTER TABLE members DROP COLUMN revoked")
    upgraded = Storage(legacy_path)
    member = upgraded.member(1, 900)
    assert member is not None and not member.revoked and member.role_granted
