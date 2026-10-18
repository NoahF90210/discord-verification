from dataclasses import dataclass

import pytest

from discord_verification.config import Settings
from discord_verification.storage import Storage
from discord_verification.verification import VerificationService


class CapturingMailer:
    def __init__(self):
        self.messages = []

    async def send_code(self, recipient, code, ttl):
        self.messages.append((recipient, code, ttl))


@dataclass
class Clock:
    now: float = 10000

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def mailer():
    return CapturingMailer()


@pytest.fixture
def storage(tmp_path):
    return Storage(tmp_path / "private" / "verification.sqlite3")


@pytest.fixture
def service(storage, mailer, clock):
    return VerificationService(storage, mailer, b"test-secret-" * 4, clock=clock)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        token="synthetic-test-token",
        guild_id=1,
        channel_id=2,
        role_id=3,
        secret=b"test-secret-" * 4,
        smtp_host="smtp.example.invalid",
        smtp_port=587,
        smtp_username="sender@example.invalid",
        smtp_password="synthetic-test-password",
        mail_from="sender@example.invalid",
        database=tmp_path / "private" / "bot.sqlite3",
    )


async def request(service, user=10, email="member@ucsb.edu", guild=1):
    await service.request(guild, user, "synthetic-member", "Test", "Member", email)
