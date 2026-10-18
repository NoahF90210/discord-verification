import asyncio
import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from conftest import request

from discord_verification.errors import VerificationError
from discord_verification.storage import Storage
from discord_verification.verification import VerificationService, normalize_email, normalize_name


@pytest.mark.parametrize(
    "email,canonical",
    [
        ("member@ucsb.edu", "member@ucsb.edu"),
        ("  MEMBER@UMAIL.UCSB.EDU  ", "member@ucsb.edu"),
    ],
)
def test_approved_domains(email, canonical):
    normalized, key = normalize_email(email, ("ucsb.edu", "umail.ucsb.edu"))
    assert normalized == email.strip().lower()
    assert key == canonical


@pytest.mark.parametrize(
    "email",
    [
        "member@ucsp.edu",
        "member@umail.ucsp.edu",
        "member@fakeucsb.edu",
        "member@ucsb.edu.evil.com",
        "member@evilucsb.edu",
        "member@dept.ucsb.edu",
        "member@umail.ucsb.edu.evil.com",
        "member@gmail.com",
        "member@ucsb.edu.",
        "member@ucsb.edu@evil.com",
        "member@@ucsb.edu",
        "member",
        "@ucsb.edu",
        "A Person <member@ucsb.edu>",
        "member+alias@ucsb.edu",
        "member..other@ucsb.edu",
        "member.@ucsb.edu",
        ".member@ucsb.edu",
        "a b@ucsb.edu",
        "a\r\nBcc:x@ucsb.edu",
        "mémber@ucsb.edu",
        "member@ucsЬ.edu",
        "a" * 65 + "@ucsb.edu",
    ],
)
def test_bad_domains_and_addresses(email):
    with pytest.raises(VerificationError):
        normalize_email(email, ("ucsb.edu", "umail.ucsb.edu"))


@pytest.mark.parametrize("name", ["", " ", "a" * 81, "a\nb", "a\x00b", "a\u200bb"])
def test_bad_names(name):
    with pytest.raises(VerificationError):
        normalize_name(name)


def test_unicode_names():
    assert normalize_name("  Zoë 李  ") == "Zoë 李"


async def test_success_replay_and_alias_duplicate(service, mailer):
    await request(service, email="member@umail.ucsb.edu")
    recipient, code, ttl = mailer.messages[-1]
    assert recipient == "member@umail.ucsb.edu" and len(code) == 6 and ttl == 600
    record = await service.verify(1, 10, code)
    assert record.email == recipient and not record.role_granted
    assert record.first_name == "Test"
    with pytest.raises(VerificationError, match="No active code"):
        await service.verify(1, 10, code)
    before = len(mailer.messages)
    await request(service, user=11, email="member@ucsb.edu")
    assert len(mailer.messages) == before
    with pytest.raises(VerificationError, match="Incorrect"):
        await service.verify(1, 11, code)
    with pytest.raises(VerificationError, match="wait"):
        await request(service)


async def test_wrong_attempts_persist_after_restart(service, mailer, clock, storage):
    await request(service)
    code = mailer.messages[-1][1]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        with pytest.raises(VerificationError, match="Incorrect"):
            await service.verify(1, 10, wrong)
    restarted = VerificationService(Storage(storage.path), mailer, service.secret, clock=clock)
    with pytest.raises(VerificationError, match="Too many"):
        await restarted.verify(1, 10, code)


async def test_format_failures_consume_attempts(service, mailer):
    await request(service)
    for value in ("", "abc", "1234567", "１２３４５６", "1\n23456"):
        with pytest.raises(VerificationError, match="Incorrect"):
            await service.verify(1, 10, value)
    with pytest.raises(VerificationError, match="Too many"):
        await service.verify(1, 10, mailer.messages[-1][1])


async def test_expiry_at_exact_boundary(service, mailer, clock):
    await request(service)
    clock.now += 600
    with pytest.raises(VerificationError, match="expired"):
        await service.verify(1, 10, mailer.messages[-1][1])


async def test_account_and_guild_binding(service, mailer):
    await request(service)
    code = mailer.messages[-1][1]
    for guild, user in ((1, 11), (2, 10)):
        with pytest.raises(VerificationError, match="No active"):
            await service.verify(guild, user, code)
    assert (await service.verify(1, 10, code)).user_id == 10


async def test_cooldown_resend_and_old_code_invalid(service, mailer, clock, monkeypatch):
    monkeypatch.setattr("discord_verification.verification.secrets.randbelow", lambda _: 123456)
    await request(service)
    old = mailer.messages[-1][1]
    with pytest.raises(VerificationError, match="wait"):
        await request(service, email="different@ucsb.edu")
    clock.now += 60
    monkeypatch.setattr("discord_verification.verification.secrets.randbelow", lambda _: 654321)
    await request(service)
    with pytest.raises(VerificationError, match="Incorrect"):
        await service.verify(1, 10, old)
    assert (await service.verify(1, 10, mailer.messages[-1][1])).user_id == 10


async def test_duplicate_pending_and_expired_release(service, mailer, clock):
    await request(service, email="member@umail.ucsb.edu")
    await request(service, user=11)
    assert len(mailer.messages) == 1
    clock.now += 600
    await request(service, user=11)
    assert (await service.verify(1, 11, mailer.messages[-1][1])).user_id == 11


async def test_hourly_user_limit_and_restart(service, mailer, clock, storage):
    for _ in range(5):
        await request(service)
        clock.now += 60
    restarted = VerificationService(Storage(storage.path), mailer, service.secret, clock=clock)
    with pytest.raises(VerificationError, match="limit"):
        await request(restarted)
    clock.now += 3600
    await request(restarted)


async def test_email_limit_survives_forget(service, mailer, clock):
    for i in range(5):
        await request(service, user=10 + i)
        await service.forget(1, 10 + i)
        clock.now += 60
    await request(service, user=99, email="member@umail.ucsb.edu")
    assert len(mailer.messages) == 5


async def test_guild_limit(service, clock, mailer):
    service.policy = replace(service.policy, guild_limit=2)
    await request(service, user=10, email="first@ucsb.edu")
    await request(service, user=11, email="second@ucsb.edu")
    await request(service, user=12, email="third@ucsb.edu")
    assert len(mailer.messages) == 2


async def test_racing_consumption_has_one_winner(service, mailer):
    await request(service)
    results = await asyncio.gather(
        *(service.verify(1, 10, mailer.messages[-1][1]) for _ in range(10)), return_exceptions=True
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(not isinstance(r, Exception) or isinstance(r, VerificationError) for r in results)


async def test_racing_reservations_have_one_winner(service, mailer):
    results = await asyncio.gather(
        *(request(service, user=i) for i in range(10, 20)), return_exceptions=True
    )
    assert all(r is None for r in results)
    assert len(mailer.messages) == 1


async def test_send_failure_invalidates_but_retains_throttle(service, mailer):
    async def fail(*args):
        raise OSError("synthetic SMTP failure")

    mailer.send_code = fail
    with pytest.raises(OSError):
        await request(service)
    with pytest.raises(VerificationError, match="No active"):
        await service.verify(1, 10, "000000")
    with pytest.raises(VerificationError, match="wait"):
        await request(service)


async def test_cancellation_cleans_pending(service, mailer):
    entered = asyncio.Event()

    async def block(*args):
        entered.set()
        await asyncio.Event().wait()

    mailer.send_code = block
    task = asyncio.create_task(request(service))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(VerificationError, match="No active"):
        await service.verify(1, 10, "123456")


async def test_cannot_consume_before_send_acknowledgement(service, mailer):
    entered, release = asyncio.Event(), asyncio.Event()
    original = mailer.send_code

    async def block(*args):
        await original(*args)
        entered.set()
        await release.wait()

    mailer.send_code = block
    task = asyncio.create_task(request(service))
    await entered.wait()
    with pytest.raises(VerificationError, match="still being sent"):
        await service.verify(1, 10, mailer.messages[-1][1])
    release.set()
    await task
    await service.verify(1, 10, mailer.messages[-1][1])


async def test_no_plaintext_code_and_private_database(service, mailer, storage):
    await request(service)
    code = mailer.messages[-1][1]
    with closing(sqlite3.connect(storage.path)) as db:
        digest, challenge_id = db.execute("SELECT digest,challenge_id FROM challenges").fetchone()
        assert digest != code and code not in (digest, challenge_id)
        assert "code" not in [r[1] for r in db.execute("PRAGMA table_info(challenges)")]
    assert storage.path.stat().st_mode & 0o777 == 0o600
    assert storage.path.parent.stat().st_mode & 0o777 == 0o700


async def test_pending_and_member_state_persist(service, mailer, storage, clock):
    await request(service)
    restarted = VerificationService(Storage(storage.path), mailer, service.secret, clock=clock)
    await restarted.verify(1, 10, mailer.messages[-1][1])
    restarted_again = VerificationService(
        Storage(storage.path), mailer, service.secret, clock=clock
    )
    assert not (await restarted_again.member(1, 10)).role_granted
    await restarted_again.mark_role_granted(1, 10)
    assert (await service.member(1, 10)).role_granted
    await restarted_again.forget(1, 10)
    assert await service.member(1, 10) is None


def test_secret_change_fails_closed(service, mailer, storage):
    with pytest.raises(ValueError, match="secret changed"):
        VerificationService(storage, mailer, b"different-secret-" * 3)


async def test_purge_removes_only_expired_state(service, clock, storage):
    await request(service)
    clock.now += 600
    await service.purge()
    with closing(sqlite3.connect(storage.path)) as db:
        assert db.execute("SELECT count(*) FROM challenges").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 1
    clock.now += 3600
    await service.purge()
    with closing(sqlite3.connect(storage.path)) as db:
        assert db.execute("SELECT count(*) FROM requests").fetchone()[0] == 0
