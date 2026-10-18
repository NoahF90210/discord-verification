import sqlite3
from contextlib import closing
from dataclasses import replace

import pytest
from conftest import request

from discord_verification.errors import DeliveryError, VerificationError
from discord_verification.storage import Storage
from discord_verification.verification import VerificationService


@pytest.mark.parametrize("status", ["verified", "pending", "unused"])
async def test_address_status_has_same_submission_code_and_throttle_responses(
    service,
    mailer,
    clock,
    storage,
    status,
    monkeypatch,
):
    monkeypatch.setattr("discord_verification.verification.secrets.randbelow", lambda _: 123456)
    if status != "unused":
        await request(service, user=900, email="target@ucsb.edu")
        if status == "verified":
            await service.verify(1, 900, "123456")
    clock.now += 60
    assert await request(service, email="target@umail.ucsb.edu") is None
    with pytest.raises(VerificationError, match="^Incorrect code"):
        await service.verify(1, 10, "654321")
    for address in ("target@ucsb.edu", "brandnew@ucsb.edu"):
        with pytest.raises(VerificationError, match="^Please wait one minute"):
            await request(service, email=address)
    # Ineligible/duplicate submissions consume the same durable per-user budget.
    for _ in range(4):
        clock.now += 60
        await request(service, email="target@umail.ucsb.edu")
    clock.now += 60
    restarted = VerificationService(Storage(storage.path), mailer, service.secret, clock=clock)
    for address in ("target@ucsb.edu", "brandnew@ucsb.edu"):
        with pytest.raises(VerificationError, match="^Email request limit"):
            await request(restarted, email=address)
    with closing(sqlite3.connect(storage.path)) as db:
        count = db.execute("SELECT count(*) FROM requests WHERE user_id=10").fetchone()[0]
        assert count == 5


async def test_suppressed_address_cannot_verify_even_with_generated_code(
    service,
    mailer,
    clock,
    monkeypatch,
):
    monkeypatch.setattr("discord_verification.verification.secrets.randbelow", lambda _: 123456)
    await request(service, user=900)
    await service.verify(1, 900, "123456")
    await request(service, user=10, email="member@umail.ucsb.edu")
    assert len(mailer.messages) == 1
    with pytest.raises(VerificationError, match="Incorrect"):
        await service.verify(1, 10, "123456")
    assert await service.member(1, 10) is None


async def test_delivery_error_does_not_expose_address_status(service, mailer, caplog):
    async def fail(*args):
        raise DeliveryError("private@ucsb.edu 123456 PRIVATE-PASSWORD")

    mailer.send_code = fail
    assert await request(service) is None
    with pytest.raises(VerificationError, match="Incorrect"):
        await service.verify(1, 10, "123456")
    with pytest.raises(VerificationError, match="wait"):
        await request(service)
    assert "Email delivery failed" in caplog.text
    assert "PRIVATE-PASSWORD" not in caplog.text and "private@ucsb.edu" not in caplog.text


async def test_server_submission_cap_bounds_rejected_address_attempts(service, mailer):
    service.policy = replace(service.policy, submission_limit=2)
    await request(service, user=900)
    await service.verify(1, 900, mailer.messages[-1][1])
    await request(service, user=10)
    for address in ("member@ucsb.edu", "unused@ucsb.edu"):
        with pytest.raises(VerificationError, match="limit"):
            await request(service, user=11, email=address)
    assert len(mailer.messages) == 1
