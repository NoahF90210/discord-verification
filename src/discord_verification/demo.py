"""Credential-free demo: real loopback SMTP, synthetic members, no Discord network."""

from __future__ import annotations

import asyncio
import os
import re
import secrets
import smtplib
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from typing import Any

from discord_verification.config import Settings
from discord_verification.errors import VerificationError
from discord_verification.mailer import SMTPMailer
from discord_verification.storage import Storage
from discord_verification.verification import VerificationService


@asynccontextmanager
async def local_smtp() -> AsyncIterator[tuple[int, list[EmailMessage]]]:
    # Optional development dependency; production SMTP uses only the standard library.
    from aiosmtpd.smtp import SMTP

    messages: list[EmailMessage] = []

    class Capture:
        async def handle_DATA(self, server: object, session: object, envelope: Any) -> str:
            message = BytesParser(policy=policy.default).parsebytes(envelope.original_content)
            assert isinstance(message, EmailMessage)
            messages.append(message)
            return "250 Captured locally; not forwarded"

    server = await asyncio.get_running_loop().create_server(
        lambda: SMTP(Capture(), enable_SMTPUTF8=False), "127.0.0.1", 0
    )
    try:
        yield server.sockets[0].getsockname()[1], messages
    finally:
        server.close()
        await server.wait_closed()


class LoopbackDemoMailer(SMTPMailer):
    """Plain SMTP only to literal loopback for this local demo, never production config."""

    def _deliver(self, message: EmailMessage) -> None:
        if self.settings.smtp_host != "127.0.0.1":
            raise ValueError("The demo must only contact loopback.")
        with smtplib.SMTP("127.0.0.1", self.settings.smtp_port, timeout=5) as client:
            client.send_message(message)


async def run_demo() -> None:
    # Honour the host scratch directory; never put demo records in the real database.
    scratch = Path(os.environ.get("TMPDIR", ".hermes/demo"))
    await asyncio.to_thread(scratch.mkdir, mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="discord-verification-demo-", dir=scratch) as directory:
        async with local_smtp() as (port, messages):
            settings = Settings(
                token="local-demo-only",
                guild_id=1,
                channel_id=2,
                role_id=3,
                secret=secrets.token_bytes(48),
                smtp_host="127.0.0.1",
                smtp_port=port,
                smtp_username="",
                smtp_password="",
                mail_from="demo@example.invalid",
                database=Path(directory) / "verification.sqlite3",
            )
            storage = Storage(settings.database)
            service = VerificationService(storage, LoopbackDemoMailer(settings), settings.secret)
            await service.request(
                1, 10, "synthetic-member", "Demo", "Member", "demo@umail.ucsb.edu"
            )
            assert len(messages) == 1
            assert messages[0]["To"] == "demo@umail.ucsb.edu"
            code = re.search(r"\b[0-9]{6}\b", str(messages[0].get_content()))
            assert code is not None
            print("PASS: generated email delivered over real loopback SMTP (not forwarded).")
            try:
                await service.verify(1, 10, "not-a-code")
            except VerificationError:
                print("PASS: incorrect code rejected.")
            else:
                raise AssertionError("Incorrect code accepted")
            await service.verify(1, 10, code.group())
            # Reopen persistent storage, rather than relying on in-memory verification state.
            restarted = VerificationService(
                Storage(settings.database), LoopbackDemoMailer(settings), settings.secret
            )
            record = await restarted.member(1, 10)
            assert record is not None and not record.role_granted
            print("PASS: verified member and pending role grant survive restart.")
            await restarted.request(1, 11, "another-member", "Other", "Member", "demo@ucsb.edu")
            assert len(messages) == 1
            try:
                await restarted.verify(1, 11, code.group())
            except VerificationError:
                print("PASS: UCSB/U-Mail alias cannot verify a second Discord account.")
            else:
                raise AssertionError("Alias duplicate accepted")
            try:
                await restarted.verify(1, 10, code.group())
            except VerificationError:
                print("PASS: consumed code cannot be replayed.")
            else:
                raise AssertionError("Code replay accepted")
            await restarted.mark_role_granted(1, 10)
            updated = await restarted.member(1, 10)
            assert updated is not None and updated.role_granted
            print("PASS: simulated role-grant completion persisted (no Discord API call).")
            print("LOCAL DEMO ONLY: no real inbox delivery or real Discord role grant tested.")
