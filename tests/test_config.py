import os
import subprocess
import sys

import pytest

from discord_verification.config import Settings
from discord_verification.errors import ConfigError


@pytest.fixture
def env():
    return {
        "DISCORD_TOKEN": "synthetic-test-token",
        "DISCORD_GUILD_ID": "1",
        "VERIFICATION_CHANNEL_ID": "2",
        "VERIFIED_ROLE_ID": "3",
        "VERIFICATION_SECRET": "s" * 48,
        "SMTP_HOST": "smtp.example.invalid",
        "SMTP_USERNAME": "sender@example.invalid",
        "SMTP_PASSWORD": "synthetic-password",
        "SMTP_FROM": "sender@example.invalid",
    }


def test_valid_settings_and_secret_repr(env):
    settings = Settings.from_env(env)
    assert settings.domains == ("ucsb.edu", "umail.ucsb.edu")
    assert settings.smtp_security == "starttls" and settings.smtp_port == 587
    for secret in (env["DISCORD_TOKEN"], env["SMTP_PASSWORD"], env["VERIFICATION_SECRET"]):
        assert secret not in repr(settings)


@pytest.mark.parametrize(
    "key,value",
    [
        ("DISCORD_TOKEN", ""),
        ("SMTP_PASSWORD", "replace_with_password"),
        ("VERIFICATION_SECRET", "short"),
        ("DISCORD_GUILD_ID", "-1"),
        ("DISCORD_GUILD_ID", "1.5"),
        ("DISCORD_GUILD_ID", str(2**64)),
        ("VERIFIED_ROLE_ID", "１"),
        ("VERIFICATION_CHANNEL_ID", "0"),
        ("SMTP_PORT", "65536"),
        ("SMTP_PORT", "abc"),
        ("SMTP_SECURITY", "plain"),
        ("SMTP_FROM", "Person <sender@example.invalid>"),
        ("SMTP_FROM", "sender@example.invalid\r\nBcc:other@example.invalid"),
        ("SMTP_HOST", "https://mail.example.invalid"),
        ("ALLOWED_EMAIL_DOMAINS", "*.ucsb.edu"),
        ("ALLOWED_EMAIL_DOMAINS", "@ucsb.edu"),
        ("ALLOWED_EMAIL_DOMAINS", "ucsb.edu,"),
        ("ALLOWED_EMAIL_DOMAINS", ""),
        ("DATABASE_PATH", ":memory:"),
        ("DATABASE_PATH", "."),
    ],
)
def test_invalid_settings(env, key, value):
    env[key] = value
    with pytest.raises(ConfigError) as error:
        Settings.from_env(env)
    assert "synthetic-password" not in str(error.value)


def test_ssl_and_explicit_domain_configuration(env):
    env.update(SMTP_SECURITY="ssl", ALLOWED_EMAIL_DOMAINS=" UCSB.EDU , UMAIL.UCSB.EDU ")
    settings = Settings.from_env(env)
    assert settings.smtp_port == 465 and settings.domains == ("ucsb.edu", "umail.ucsb.edu")


def test_cli_help_and_preflight(env, tmp_path):
    help_result = subprocess.run(
        [sys.executable, "-m", "discord_verification", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--demo" in help_result.stdout
    result = subprocess.run(
        [sys.executable, "-m", "discord_verification", "--check"],
        env={**os.environ, **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and "No Discord or SMTP connection" in result.stdout
    assert not (tmp_path / "data").exists()
    env["SMTP_PASSWORD"] = ""
    bad = subprocess.run(
        [sys.executable, "-m", "discord_verification", "--check"],
        env={**os.environ, **env},
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert bad.returncode == 2 and "SMTP_PASSWORD" in bad.stderr
