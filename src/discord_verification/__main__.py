"""CLI entry point: validate configuration, run a local demo, or connect the bot."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import dotenv_values, load_dotenv

from discord_verification.config import Settings
from discord_verification.errors import ConfigError


def main() -> int:
    parser = argparse.ArgumentParser(description="Assistive Tech Discord email verification")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Validate settings without connecting")
    mode.add_argument(
        "--demo", action="store_true", help="Loopback SMTP demo; no credentials needed"
    )
    args = parser.parse_args()
    if args.demo:
        from discord_verification.demo import run_demo

        try:
            asyncio.run(run_demo())
        except ImportError:
            print("The demo needs development dependencies. Run uv sync --locked.", file=sys.stderr)
            return 1
        return 0

    load_dotenv(dotenv_path=".env", override=False)
    environment = dict(os.environ)
    # Explicit local SMTP settings win over stale credentials inherited from the shell.
    # Read them literally so ${...} in a password cannot expand from that stale shell.
    local = dotenv_values(dotenv_path=".env", interpolate=False)
    for key in (
        "SMTP_HOST",
        "SMTP_PORT",
        "SMTP_SECURITY",
        "SMTP_USERNAME",
        "SMTP_PASSWORD",
        "SMTP_FROM",
    ):
        value = local.get(key)
        if value is not None:
            environment[key] = value
    try:
        settings = Settings.from_env(environment)
    except ConfigError as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    if args.check:
        print("Configuration is valid. No Discord or SMTP connection was made.")
        return 0

    from discord_verification.bot import VerificationClient
    from discord_verification.mailer import SMTPMailer
    from discord_verification.storage import Storage
    from discord_verification.verification import VerificationService

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Library tracebacks can contain interaction data. Our handlers log only safe categories.
    logging.getLogger("discord").setLevel(logging.CRITICAL)
    try:
        storage = Storage(settings.database)
        service = VerificationService(
            storage, SMTPMailer(settings), settings.secret, settings.domains
        )
        client = VerificationClient(settings, service)
        client.run(settings.token, log_handler=None)
    except Exception as error:
        print(
            f"Startup/run failed ({type(error).__name__}). Check configuration and permissions.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
