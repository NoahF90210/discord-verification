import asyncio
import re
import smtplib
import ssl
import subprocess
from dataclasses import replace
from email import policy
from email.parser import BytesParser
from unittest.mock import MagicMock, patch

import pytest
from aiosmtpd.smtp import SMTP

from discord_verification.demo import LoopbackDemoMailer, local_smtp, run_demo
from discord_verification.errors import DeliveryError
from discord_verification.mailer import SMTPMailer
from discord_verification.storage import Storage
from discord_verification.verification import VerificationService


async def test_real_loopback_smtp_end_to_end(settings):
    async with local_smtp() as (port, messages):
        configured = replace(settings, smtp_host="127.0.0.1", smtp_port=port)
        service = VerificationService(
            Storage(settings.database), LoopbackDemoMailer(configured), settings.secret
        )
        await service.request(1, 10, "synthetic", "Test", "Member", "member@ucsb.edu")
        assert len(messages) == 1
        message = messages[0]
        assert message["From"] == settings.mail_from
        assert message["To"] == "member@ucsb.edu"
        assert message["Date"] and message["Message-ID"]
        code = re.search(r"\b[0-9]{6}\b", message.get_content()).group()
        assert (await service.verify(1, 10, code)).email == "member@ucsb.edu"


async def test_production_starttls_auth_and_delivery(settings, tmp_path, monkeypatch):
    key, cert = tmp_path / "server.key", tmp_path / "server.pem"
    await asyncio.to_thread(
        subprocess.run,
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert, key)
    client_context = ssl.create_default_context(cafile=str(cert))
    monkeypatch.setattr(
        "discord_verification.mailer.ssl.create_default_context", lambda: client_context
    )
    messages = []

    class Capture:
        async def handle_DATA(self, server, session, envelope):
            assert session.ssl is not None
            assert session.authenticated
            messages.append(
                BytesParser(policy=policy.default).parsebytes(envelope.original_content)
            )
            return "250 OK"

    server = await asyncio.get_running_loop().create_server(
        lambda: SMTP(
            Capture(),
            tls_context=server_context,
            require_starttls=True,
            auth_required=True,
            auth_callback=lambda mechanism, login, password: (
                login == b"sender@example.invalid" and password == b"synthetic-test-password"
            ),
        ),
        "127.0.0.1",
        0,
    )
    try:
        configured = replace(
            settings, smtp_host="127.0.0.1", smtp_port=server.sockets[0].getsockname()[1]
        )
        await SMTPMailer(configured).send_code("member@ucsb.edu", "123456", 600)
        assert len(messages) == 1 and "123456" in messages[0].get_content()
    finally:
        server.close()
        await server.wait_closed()


async def test_starttls_before_credentials(settings):
    with patch("discord_verification.mailer.smtplib.SMTP") as factory:
        client = factory.return_value.__enter__.return_value
        # STARTTLS occurs on the pre-context client, login on the with-bound client.
        factory.return_value.__enter__.return_value = factory.return_value
        client = factory.return_value
        client.send_message.return_value = {}
        await SMTPMailer(settings).send_code("member@ucsb.edu", "123456", 600)
        names = [call[0] for call in client.method_calls]
        assert names.index("starttls") < names.index("login") < names.index("send_message")
        assert factory.call_args.kwargs["timeout"] == 15


async def test_no_plaintext_fallback_or_secret_errors(settings):
    with patch("discord_verification.mailer.smtplib.SMTP") as factory:
        factory.return_value.__enter__.return_value = factory.return_value
        factory.return_value.starttls.side_effect = smtplib.SMTPNotSupportedError(
            "private SMTP message with member@ucsb.edu and 123456"
        )
        with pytest.raises(DeliveryError) as error:
            await SMTPMailer(settings).send_code("member@ucsb.edu", "123456", 600)
        assert "member@" not in str(error.value) and "123456" not in str(error.value)
        factory.return_value.login.assert_not_called()
        factory.return_value.send_message.assert_not_called()


async def test_implicit_tls(settings):
    with patch("discord_verification.mailer.smtplib.SMTP_SSL") as factory:
        client = factory.return_value
        client.send_message.return_value = {}
        await SMTPMailer(replace(settings, smtp_security="ssl", smtp_port=465)).send_code(
            "member@ucsb.edu", "123456", 600
        )
        assert factory.call_args.kwargs["context"].verify_mode == ssl.CERT_REQUIRED
        client.starttls.assert_not_called()
        client.login.assert_called_once()


async def test_recipient_refusal(settings):
    with patch("discord_verification.mailer.smtplib.SMTP") as factory:
        factory.return_value.__enter__.return_value = factory.return_value
        factory.return_value.send_message.return_value = {"member@ucsb.edu": (550, b"refused")}
        with pytest.raises(DeliveryError):
            await SMTPMailer(settings).send_code("member@ucsb.edu", "123456", 600)


async def test_demo_is_loopback_only(settings):
    with pytest.raises(ValueError, match="loopback"):
        LoopbackDemoMailer(settings)._deliver(MagicMock())


async def test_complete_local_demo(capsys):
    await run_demo()
    output = capsys.readouterr().out
    assert output.count("PASS:") == 6
    assert "no real inbox delivery or real Discord role grant" in output
