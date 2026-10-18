"""Environment-based configuration. Never print the settings object or secrets."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from discord_verification.errors import ConfigError


@dataclass(frozen=True)
class Settings:
    token: str = field(repr=False)
    guild_id: int
    channel_id: int
    role_id: int
    secret: bytes = field(repr=False)
    smtp_host: str
    smtp_port: int
    smtp_username: str = field(repr=False)
    smtp_password: str = field(repr=False)
    mail_from: str
    smtp_security: Literal["starttls", "ssl"] = "starttls"
    database: Path = Path("data/verification.sqlite3")
    domains: tuple[str, ...] = ("ucsb.edu", "umail.ucsb.edu")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Settings:
        def required(name: str) -> str:
            value = env.get(name, "").strip()
            if not value or value.startswith("replace_"):
                raise ConfigError(f"Set {name} in your local environment or .env file.")
            if any(ord(c) < 32 or ord(c) == 127 for c in value):
                raise ConfigError(f"{name} must not contain control characters.")
            return value

        def number(name: str, default: str | None = None) -> int:
            value = env.get(name, default or "").strip()
            if not value.isascii() or not value.isdecimal() or not 0 < int(value) < 2**63:
                raise ConfigError(f"{name} must be a positive integer below 2^63.")
            return int(value)

        secret = required("VERIFICATION_SECRET").encode("utf-8")
        if len(secret) < 32:
            raise ConfigError("VERIFICATION_SECRET must contain at least 32 bytes.")
        security = env.get("SMTP_SECURITY", "starttls").strip().lower()
        if security not in {"starttls", "ssl"}:
            raise ConfigError("SMTP_SECURITY must be starttls or ssl; plaintext is forbidden.")
        port = number("SMTP_PORT", "587" if security == "starttls" else "465")
        if port > 65535:
            raise ConfigError("SMTP_PORT must be between 1 and 65535.")
        host = required("SMTP_HOST")
        try:
            ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9.-]+", host) or ".." in host:
                raise ConfigError("SMTP_HOST must be a hostname or IP address.") from None
        sender = required("SMTP_FROM")
        if not re.fullmatch(r"[A-Za-z0-9._+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", sender):
            raise ConfigError("SMTP_FROM must be a single plain email address.")
        domains = tuple(
            d.strip().lower()
            for d in env.get("ALLOWED_EMAIL_DOMAINS", "ucsb.edu,umail.ucsb.edu").split(",")
        )
        if not domains or any(
            not re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+", d
            )
            for d in domains
        ):
            raise ConfigError(
                "ALLOWED_EMAIL_DOMAINS must list exact domains, without @ or wildcards."
            )
        database = Path(env.get("DATABASE_PATH", "data/verification.sqlite3"))
        if str(database) in {".", ":memory:"} or database.is_dir():
            raise ConfigError("DATABASE_PATH must be a persistent database file, not a directory.")
        return cls(
            token=required("DISCORD_TOKEN"),
            guild_id=number("DISCORD_GUILD_ID"),
            channel_id=number("VERIFICATION_CHANNEL_ID"),
            role_id=number("VERIFIED_ROLE_ID"),
            secret=secret,
            smtp_host=host,
            smtp_port=port,
            smtp_username=required("SMTP_USERNAME"),
            smtp_password=required("SMTP_PASSWORD"),
            mail_from=sender,
            smtp_security="ssl" if security == "ssl" else "starttls",
            database=database,
            domains=domains,
        )
