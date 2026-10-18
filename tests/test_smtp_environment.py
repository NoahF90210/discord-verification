"""Exercise the actual CLI loader with synthetic .env/shell conflicts, never real secrets."""

import asyncio
import os
import ssl
import subprocess

import pytest
from aiosmtpd.smtp import SMTP
from dotenv import set_key

from discord_verification import __main__ as cli
from discord_verification.bot import VerificationClient
from discord_verification.config import Settings

SMTP_KEYS = (
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SECURITY",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
    "SMTP_FROM",
)


@pytest.fixture
def values(settings):
    return {
        "DISCORD_TOKEN": settings.token,
        "DISCORD_GUILD_ID": str(settings.guild_id),
        "VERIFICATION_CHANNEL_ID": str(settings.channel_id),
        "VERIFIED_ROLE_ID": str(settings.role_id),
        "VERIFICATION_SECRET": settings.secret.decode(),
        "DATABASE_PATH": str(settings.database),
        "ALLOWED_EMAIL_DOMAINS": ",".join(settings.domains),
        "SMTP_HOST": settings.smtp_host,
        "SMTP_PORT": str(settings.smtp_port),
        "SMTP_SECURITY": settings.smtp_security,
        "SMTP_USERNAME": settings.smtp_username,
        "SMTP_PASSWORD": settings.smtp_password,
        "SMTP_FROM": settings.mail_from,
    }


def configure(monkeypatch, tmp_path, values, local):
    monkeypatch.chdir(tmp_path)
    # Track both present and absent keys so load_dotenv cannot leak state between tests.
    for key in values:
        monkeypatch.delenv(key, raising=False)
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    if local is not None:
        for key, value in local.items():
            set_key(str(tmp_path / ".env"), key, value)
    monkeypatch.setattr("sys.argv", ["discord-verification", "--check"])
    captured = []
    original = Settings.from_env

    def capture(env):
        captured.append(dict(env))
        return original(env)

    monkeypatch.setattr(Settings, "from_env", staticmethod(capture))
    return captured


@pytest.mark.parametrize("key", SMTP_KEYS)
def test_local_smtp_wins_in_normal_cli(monkeypatch, tmp_path, values, key):
    local = dict(values)
    shell = dict(values)
    shell.update(
        SMTP_HOST="stale.example.invalid",
        SMTP_PORT="465",
        SMTP_SECURITY="ssl",
        SMTP_USERNAME="stale@example.invalid",
        SMTP_PASSWORD="stale-password",
        SMTP_FROM="stale@example.invalid",
    )
    captured = configure(monkeypatch, tmp_path, shell, local)
    assert cli.main() == 0
    assert captured[-1][key] == local[key]
    # Selecting local SMTP must not rewrite the caller's environment.
    assert os.environ[key] == shell[key]


@pytest.mark.parametrize("local", [None, {}, {"SMTP_PASSWORD": "local-password"}])
def test_missing_file_or_partial_file_keeps_environment_fallback(
    monkeypatch,
    tmp_path,
    values,
    local,
):
    captured = configure(monkeypatch, tmp_path, values, local)
    assert cli.main() == 0
    expected = {**values, **(local or {})}
    assert all(captured[-1][key] == expected[key] for key in SMTP_KEYS)


@pytest.mark.parametrize("password", ["", "replace_with_app_specific_password"])
def test_invalid_local_password_does_not_fall_back_to_shell(
    monkeypatch,
    tmp_path,
    values,
    password,
    capsys,
):
    configure(monkeypatch, tmp_path, values, {"SMTP_PASSWORD": password})
    assert cli.main() == 2
    assert "SMTP_PASSWORD" in capsys.readouterr().err


def test_local_smtp_secret_is_literal_not_interpolated_from_shell(monkeypatch, tmp_path, values):
    captured = configure(monkeypatch, tmp_path, values, {"SMTP_PASSWORD": "local-${SMTP_PASSWORD}"})
    assert cli.main() == 0
    assert captured[-1]["SMTP_PASSWORD"] == "local-${SMTP_PASSWORD}"


def test_other_environment_precedence_unchanged(monkeypatch, tmp_path, values):
    captured = configure(
        monkeypatch,
        tmp_path,
        values,
        {
            "DISCORD_TOKEN": "other-token",
            "DISCORD_GUILD_ID": "99",
            "DATABASE_PATH": "other.sqlite3",
            "VERIFICATION_SECRET": "other-secret-" * 4,
            "ALLOWED_EMAIL_DOMAINS": "other.example.invalid",
            "SMTP_UNRELATED": "local-value",
        },
    )
    monkeypatch.setenv("SMTP_UNRELATED", "shell-value")
    assert cli.main() == 0
    for key in (
        "DISCORD_TOKEN",
        "DISCORD_GUILD_ID",
        "DATABASE_PATH",
        "VERIFICATION_SECRET",
        "ALLOWED_EMAIL_DOMAINS",
    ):
        assert captured[-1][key] == values[key]
    assert captured[-1]["SMTP_UNRELATED"] == "shell-value"


def test_file_only_and_bare_assignment_fallback(monkeypatch, tmp_path, values):
    captured = configure(monkeypatch, tmp_path, values, values)
    for key in values:
        monkeypatch.delenv(key)
    assert cli.main() == 0
    assert all(captured[-1][key] == value for key, value in values.items())
    # A dotenv name without '=' supplies no value and must not override the environment.
    (tmp_path / ".env").write_text("SMTP_PASSWORD\n")
    monkeypatch.setenv("SMTP_PASSWORD", values["SMTP_PASSWORD"])
    assert cli.main() == 0
    assert captured[-1]["SMTP_PASSWORD"] == values["SMTP_PASSWORD"]


def test_conflicting_settings_never_print_secrets(monkeypatch, tmp_path, values, capsys, caplog):
    configure(
        monkeypatch,
        tmp_path,
        values,
        {
            "SMTP_USERNAME": "local-private@example.invalid",
            "SMTP_PASSWORD": "local-private-password",
        },
    )
    assert cli.main() == 0
    output = capsys.readouterr()
    combined = output.out + output.err + caplog.text
    for secret in (
        values["SMTP_PASSWORD"],
        "local-private-password",
        "local-private@example.invalid",
    ):
        assert secret not in combined


async def test_regular_cli_startup_delivers_with_local_credentials_over_real_starttls(
    monkeypatch,
    tmp_path,
    values,
):
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
    trusted_context = ssl.create_default_context(cafile=str(cert))
    messages = []

    class Capture:
        async def handle_DATA(self, server, session, envelope):
            assert session.ssl is not None and session.authenticated
            messages.append(envelope)
            return "250 Captured locally; not forwarded"

    server = await asyncio.get_running_loop().create_server(
        lambda: SMTP(
            Capture(),
            tls_context=server_context,
            require_starttls=True,
            auth_required=True,
            auth_callback=lambda mechanism, login, password: (
                login == values["SMTP_USERNAME"].encode()
                and password == values["SMTP_PASSWORD"].encode()
            ),
        ),
        "127.0.0.1",
        0,
    )
    try:
        local = {
            **values,
            "SMTP_HOST": "127.0.0.1",
            "SMTP_PORT": str(server.sockets[0].getsockname()[1]),
        }
        shell = {
            **values,
            "SMTP_HOST": "stale.example.invalid",
            "SMTP_PORT": "9",
            "SMTP_SECURITY": "ssl",
            "SMTP_USERNAME": "stale@example.invalid",
            "SMTP_PASSWORD": "stale-password",
            "SMTP_FROM": "stale@example.invalid",
        }
        captured = configure(monkeypatch, tmp_path, shell, local)
        monkeypatch.setattr("sys.argv", ["discord-verification"])
        monkeypatch.setattr(
            "discord_verification.mailer.ssl.create_default_context", lambda: trusted_context
        )

        def run(client, token, **kwargs):
            assert all(captured[-1][name] == local[name] for name in SMTP_KEYS)
            asyncio.run(
                client.service.request(1, 10, "synthetic", "Test", "Member", "member@ucsb.edu")
            )

        # Only Discord's external connection is replaced; CLI/config/storage/SMTP are real.
        monkeypatch.setattr(VerificationClient, "run", run)
        result = await asyncio.to_thread(cli.main)
        assert result == 0
        assert len(messages) == 1
        assert messages[0].mail_from == local["SMTP_FROM"]
        assert messages[0].rcpt_tos == ["member@ucsb.edu"]
        assert b"Assistive Tech Discord verification code" in messages[0].original_content
    finally:
        server.close()
        await server.wait_closed()
