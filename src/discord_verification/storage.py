"""Short SQLite transactions; call through asyncio.to_thread from async code."""

from __future__ import annotations

import hmac
import os
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from discord_verification.errors import VerificationError
from discord_verification.schema import CHALLENGES_SQL, RESERVED_EMAIL_SQL, migrate_v1


@dataclass(frozen=True)
class Policy:
    ttl: int = 600
    max_attempts: int = 5
    cooldown: int = 60
    window: int = 3600
    user_limit: int = 5
    email_limit: int = 5
    guild_limit: int = 100
    submission_limit: int = 500


@dataclass(frozen=True)
class Member:
    guild_id: int
    user_id: int
    username: str
    first_name: str
    last_name: str
    email: str
    verified_at: float
    role_granted: bool
    revoked: bool


@dataclass(frozen=True)
class Challenge:
    guild_id: int
    user_id: int
    username: str
    first_name: str
    last_name: str
    email: str
    email_key: str
    challenge_id: str
    digest: str
    created: float


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink():
            raise ValueError("The database must not be a symbolic link.")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        path.chmod(0o600)
        with self._connection() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("Unsupported database version; restore compatible code or backup.")
            if version == 1:
                migrate_v1(db)
            db.executescript(f"""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS members (
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    username TEXT NOT NULL, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
                    email TEXT NOT NULL, email_key TEXT NOT NULL, verified_at REAL NOT NULL,
                    role_granted INTEGER NOT NULL DEFAULT 0, revoked INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (guild_id, user_id), UNIQUE (guild_id, email_key)
                );
                {CHALLENGES_SQL};
                {RESERVED_EMAIL_SQL};
                CREATE TABLE IF NOT EXISTS requests (
                    guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                    email_key TEXT NOT NULL, created REAL NOT NULL, sent INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS requests_guild ON requests(guild_id, created);
                CREATE INDEX IF NOT EXISTS requests_user ON requests(guild_id, user_id, created);
                CREATE INDEX IF NOT EXISTS requests_email ON requests(guild_id, email_key, created);
                PRAGMA user_version=2;
            """)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA secure_delete=ON")
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def bind_secret(self, fingerprint: str) -> None:
        with self._transaction() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='secret_fingerprint'").fetchone()
            if row and not hmac.compare_digest(row["value"], fingerprint):
                raise ValueError(
                    "Verification secret changed; restore the original secret with this database."
                )
            db.execute(
                "INSERT OR IGNORE INTO metadata VALUES ('secret_fingerprint', ?)", (fingerprint,)
            )

    @staticmethod
    def _member(row: sqlite3.Row) -> Member:
        return Member(
            guild_id=row["guild_id"],
            user_id=row["user_id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            email=row["email"],
            verified_at=row["verified_at"],
            role_granted=bool(row["role_granted"]),
            revoked=bool(row["revoked"]),
        )

    def member(self, guild: int, user: int) -> Member | None:
        with self._connection() as db:
            row = db.execute(
                "SELECT * FROM members WHERE guild_id=? AND user_id=?", (guild, user)
            ).fetchone()
            return self._member(row) if row else None

    def reserve(self, challenge: Challenge, policy: Policy) -> bool:
        c, p = challenge, policy
        own_error: str | None = None
        eligible = False
        with self._transaction() as db:
            db.execute("DELETE FROM requests WHERE created<=?", (c.created - p.window,))
            db.execute("DELETE FROM challenges WHERE expires<=?", (c.created,))
            rows = db.execute(
                "SELECT user_id,email_key,created,sent FROM requests "
                "WHERE guild_id=? AND created>?",
                (c.guild_id, c.created - p.window),
            ).fetchall()
            user_requests = [r for r in rows if r["user_id"] == c.user_id]
            # User-only throttles run BEFORE any address-status lookup. Rejected address
            # submissions count too; do not roll these counters back with an exception.
            if any(r["created"] > c.created - p.cooldown for r in user_requests):
                raise VerificationError("Please wait one minute before requesting another email.")
            if len(user_requests) >= p.user_limit or len(rows) >= p.submission_limit:
                raise VerificationError("Email request limit reached. Try again in an hour.")
            db.execute(
                "INSERT INTO requests(guild_id,user_id,email_key,created,sent) VALUES(?,?,?,?,0)",
                (c.guild_id, c.user_id, c.email_key, c.created),
            )
            request_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            if db.execute(
                "SELECT 1 FROM members WHERE guild_id=? AND user_id=?", (c.guild_id, c.user_id)
            ).fetchone():
                own_error = "You are already verified. Use Retry access or contact an officer."
            else:
                email_requests = [r for r in rows if r["email_key"] == c.email_key and r["sent"]]
                eligible = (
                    sum(bool(r["sent"]) for r in rows) < p.guild_limit
                    and len(email_requests) < p.email_limit
                    and not any(r["created"] > c.created - p.cooldown for r in email_requests)
                    and not db.execute(
                        "SELECT 1 FROM members WHERE guild_id=? AND email_key=?",
                        (c.guild_id, c.email_key),
                    ).fetchone()
                    and not db.execute(
                        "SELECT 1 FROM challenges WHERE guild_id=? AND email_key=? "
                        "AND user_id!=? AND eligible=1",
                        (c.guild_id, c.email_key, c.user_id),
                    ).fetchone()
                )
                db.execute(
                    "DELETE FROM challenges WHERE guild_id=? AND user_id=?", (c.guild_id, c.user_id)
                )
                # Suppressed addresses get a non-verifiable decoy challenge. This keeps
                # request AND code-entry responses from revealing another member's status.
                db.execute(
                    """INSERT INTO challenges
                    (guild_id,user_id,username,first_name,last_name,email,email_key,
                     challenge_id,digest,created,expires,eligible,ready)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        c.guild_id,
                        c.user_id,
                        c.username,
                        c.first_name,
                        c.last_name,
                        c.email,
                        c.email_key,
                        c.challenge_id,
                        c.digest,
                        c.created,
                        c.created + p.ttl,
                        int(eligible),
                        int(not eligible),
                    ),
                )
                if eligible:
                    db.execute("UPDATE requests SET sent=1 WHERE rowid=?", (request_id,))
        if own_error:
            raise VerificationError(own_error)
        return eligible

    def activate(self, guild: int, user: int, challenge_id: str, now: float) -> None:
        with self._transaction() as db:
            changed = db.execute(
                """UPDATE challenges SET ready=1
                WHERE guild_id=? AND user_id=? AND challenge_id=? AND expires>?""",
                (guild, user, challenge_id, now),
            ).rowcount
        if changed != 1:
            raise VerificationError("This request expired or was replaced. Request a new code.")

    def suppress(self, guild: int, user: int, challenge_id: str) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE challenges SET eligible=0,ready=1 "
                "WHERE guild_id=? AND user_id=? AND challenge_id=?",
                (guild, user, challenge_id),
            )

    def discard(self, guild: int, user: int, challenge_id: str) -> None:
        with self._transaction() as db:
            db.execute(
                "DELETE FROM challenges WHERE guild_id=? AND user_id=? AND challenge_id=?",
                (guild, user, challenge_id),
            )

    def consume(
        self,
        guild: int,
        user: int,
        now: float,
        policy: Policy,
        digest: Callable[[str], str],
    ) -> Member:
        error: str | None = None
        member: Member | None = None
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM challenges WHERE guild_id=? AND user_id=?", (guild, user)
            ).fetchone()
            if row is None:
                error = (
                    "No active code. Start verification or use Retry access if already verified."
                )
            elif row["expires"] <= now:
                db.execute("DELETE FROM challenges WHERE guild_id=? AND user_id=?", (guild, user))
                error = "Your code expired. Request a new one."
            elif not row["ready"]:
                error = "Email is still being sent. Please try again shortly."
            elif row["attempts"] >= policy.max_attempts:
                error = "Too many incorrect attempts. Request a new code when the cooldown ends."
            elif not row["eligible"] or not hmac.compare_digest(
                row["digest"], digest(row["challenge_id"])
            ):
                db.execute(
                    "UPDATE challenges SET attempts=attempts+1 WHERE guild_id=? AND user_id=?",
                    (guild, user),
                )
                error = "Incorrect code. Check your email and try again."
            else:
                db.execute(
                    """INSERT INTO members
                    (guild_id,user_id,username,first_name,last_name,email,email_key,verified_at)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        guild,
                        user,
                        row["username"],
                        row["first_name"],
                        row["last_name"],
                        row["email"],
                        row["email_key"],
                        now,
                    ),
                )
                db.execute("DELETE FROM challenges WHERE guild_id=? AND user_id=?", (guild, user))
                stored = db.execute(
                    "SELECT * FROM members WHERE guild_id=? AND user_id=?", (guild, user)
                ).fetchone()
                member = self._member(stored)
        # Raise after committing: wrong-attempt counters must not roll back.
        if error:
            raise VerificationError(error)
        assert member is not None
        return member

    def mark_role_granted(self, guild: int, user: int) -> None:
        with self._transaction() as db:
            db.execute(
                "UPDATE members SET role_granted=1 WHERE guild_id=? AND user_id=?", (guild, user)
            )

    def begin_revocation(self, guild: int, user: int) -> None:
        with self._transaction() as db:
            db.execute("UPDATE members SET revoked=1 WHERE guild_id=? AND user_id=?", (guild, user))
            db.execute("DELETE FROM challenges WHERE guild_id=? AND user_id=?", (guild, user))

    def forget(self, guild: int, user: int) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM challenges WHERE guild_id=? AND user_id=?", (guild, user))
            db.execute("DELETE FROM members WHERE guild_id=? AND user_id=?", (guild, user))
            # Retain short-lived HMAC-only abuse counters, even after deletion.

    def purge(self, now: float, policy: Policy) -> None:
        with self._transaction() as db:
            db.execute("DELETE FROM challenges WHERE expires<=?", (now,))
            db.execute("DELETE FROM requests WHERE created<=?", (now - policy.window,))
