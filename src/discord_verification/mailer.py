"""TLS-only production SMTP, using an ordinary mailbox rather than a sending API."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Protocol

from discord_verification.config import Settings
from discord_verification.errors import DeliveryError


class Mailer(Protocol):
    async def send_code(self, recipient: str, code: str, ttl: int) -> None: ...


class SMTPMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def message(self, recipient: str, code: str, ttl: int) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.settings.mail_from
        msg["To"] = recipient
        msg["Subject"] = "Assistive Tech Discord verification code"
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid()
        msg.set_content(
            f"Your Assistive Tech Discord verification code is: {code}\n\n"
            f"It expires in {ttl // 60} minutes. Enter it only in the Discord bot's "
            "private Enter code form. Never share it with another person.\n\n"
            "If you did not request this, ignore this email. You do not need to reply.\n"
        )
        return msg

    def _deliver(self, message: EmailMessage) -> None:
        s = self.settings
        context = ssl.create_default_context()
        if s.smtp_security == "ssl":
            client: smtplib.SMTP = smtplib.SMTP_SSL(
                s.smtp_host, s.smtp_port, timeout=15, context=context
            )
        else:
            client = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
        with client:
            if s.smtp_security == "starttls":
                client.ehlo()
                client.starttls(context=context)
                client.ehlo()
            client.login(s.smtp_username, s.smtp_password)
            refused = client.send_message(message)
            if refused:
                raise DeliveryError("Email could not be sent. Please try again later.")

    async def send_code(self, recipient: str, code: str, ttl: int) -> None:
        message = self.message(recipient, code, ttl)
        try:
            await asyncio.to_thread(self._deliver, message)
        except (OSError, smtplib.SMTPException, ValueError):
            # Never forward SMTP errors: they may contain addresses or credentials.
            raise DeliveryError("Email could not be sent. Please try again later.") from None
