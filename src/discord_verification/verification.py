"""Discord-independent verification logic with durable abuse prevention."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
import secrets
import time
import unicodedata
from collections.abc import Callable

from discord_verification.errors import DeliveryError, VerificationError
from discord_verification.mailer import Mailer
from discord_verification.storage import Challenge, Member, Policy, Storage


def normalize_email(value: str, domains: tuple[str, ...]) -> tuple[str, str]:
    email = value.strip().lower()
    if len(email) > 254 or not email.isascii() or email.count("@") != 1:
        raise VerificationError("Enter a valid university email address.")
    local, domain = email.split("@")
    if (
        not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", local)
        or local.endswith(".")
        or ".." in local
    ):
        raise VerificationError("Use your primary university address, without plus aliases.")
    if domain not in domains:
        raise VerificationError("Use an approved university email domain listed in the panel.")
    canonical_domain = "ucsb.edu" if domain == "umail.ucsb.edu" else domain
    return email, f"{local}@{canonical_domain}"


def normalize_name(value: str) -> str:
    name = value.strip()
    if not 1 <= len(name) <= 80 or any(unicodedata.category(c).startswith("C") for c in name):
        raise VerificationError("Names must contain 1–80 characters without control characters.")
    return name


class VerificationService:
    def __init__(
        self,
        storage: Storage,
        mailer: Mailer,
        secret: bytes,
        domains: tuple[str, ...] = ("ucsb.edu", "umail.ucsb.edu"),
        *,
        policy: Policy | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if len(secret) < 32:
            raise ValueError("Use a random verification secret of at least 32 bytes.")
        self.storage, self.mailer, self.secret = storage, mailer, secret
        self.domains, self.policy, self.clock = domains, policy or Policy(), clock
        self.storage.bind_secret(self._mac("database-secret-fingerprint"))

    def _mac(self, value: str) -> str:
        return hmac.new(self.secret, value.encode(), hashlib.sha256).hexdigest()

    def _code_digest(self, guild: int, user: int, challenge_id: str, code: str) -> str:
        return self._mac(f"code|{guild}|{user}|{challenge_id}|{code}")

    async def request(
        self,
        guild: int,
        user: int,
        username: str,
        first_name: str,
        last_name: str,
        email: str,
    ) -> None:
        normalized, canonical = normalize_email(email, self.domains)
        first, last = normalize_name(first_name), normalize_name(last_name)
        code = f"{secrets.randbelow(1_000_000):06d}"
        challenge_id = secrets.token_urlsafe(24)
        challenge = Challenge(
            guild,
            user,
            username[:100],
            first,
            last,
            normalized,
            self._mac(f"email|{guild}|{canonical}"),
            challenge_id,
            self._code_digest(guild, user, challenge_id, code),
            self.clock(),
        )
        eligible = await asyncio.to_thread(self.storage.reserve, challenge, self.policy)
        if not eligible:
            return
        try:
            await self.mailer.send_code(normalized, code, self.policy.ttl)
            await asyncio.to_thread(self.storage.activate, guild, user, challenge_id, self.clock())
        except DeliveryError:
            # Keep public request/code-entry responses independent of another email's
            # status, including when the SMTP provider fails. Operators get a safe log.
            await asyncio.to_thread(self.storage.suppress, guild, user, challenge_id)
            logging.getLogger(__name__).warning("Email delivery failed; code invalidated.")
        except BaseException:
            # Cancellation and unexpected failures must not leave usable phantom requests.
            await asyncio.shield(asyncio.to_thread(self.storage.discard, guild, user, challenge_id))
            raise

    async def verify(self, guild: int, user: int, code: str) -> Member:
        stripped = code.strip()
        # Format failures still consume an attempt; no unlimited alternate path.
        candidate = stripped if re.fullmatch(r"[0-9]{6}", stripped) else "invalid"
        return await asyncio.to_thread(
            self.storage.consume,
            guild,
            user,
            self.clock(),
            self.policy,
            lambda challenge_id: self._code_digest(guild, user, challenge_id, candidate),
        )

    async def member(self, guild: int, user: int) -> Member | None:
        return await asyncio.to_thread(self.storage.member, guild, user)

    async def mark_role_granted(self, guild: int, user: int) -> None:
        await asyncio.to_thread(self.storage.mark_role_granted, guild, user)

    async def begin_revocation(self, guild: int, user: int) -> None:
        await asyncio.to_thread(self.storage.begin_revocation, guild, user)

    async def forget(self, guild: int, user: int) -> None:
        await asyncio.to_thread(self.storage.forget, guild, user)

    async def purge(self) -> None:
        await asyncio.to_thread(self.storage.purge, self.clock(), self.policy)
