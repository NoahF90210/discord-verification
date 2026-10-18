"""Private Discord interactions. No privileged intents or message scanning."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from weakref import WeakValueDictionary

import discord
from discord import app_commands
from discord.ext import tasks

from discord_verification.config import Settings
from discord_verification.errors import VerificationError
from discord_verification.verification import VerificationService

log = logging.getLogger(__name__)

# Positive classification: unknown/future Discord permission bits fail closed.
# Channel-specific overwrites still require the operator's separate permission audit.
SAFE_MEMBER_PERMISSIONS = discord.Permissions(
    add_reactions=True,
    stream=True,
    view_channel=True,
    send_messages=True,
    embed_links=True,
    attach_files=True,
    read_message_history=True,
    use_external_emojis=True,
    connect=True,
    speak=True,
    use_voice_activation=True,
    change_nickname=True,
    use_application_commands=True,
    request_to_speak=True,
    create_public_threads=True,
    create_private_threads=True,
    use_external_stickers=True,
    send_messages_in_threads=True,
    use_embedded_activities=True,
    use_soundboard=True,
    use_external_sounds=True,
    send_voice_messages=True,
    set_voice_channel_status=True,
    send_polls=True,
    use_external_apps=True,
)


class VerificationClient(discord.Client):
    def __init__(self, settings: Settings, service: VerificationService) -> None:
        super().__init__(
            intents=discord.Intents(guilds=True), allowed_mentions=discord.AllowedMentions.none()
        )
        self.settings, self.service = settings, service
        self.tree = app_commands.CommandTree(self)
        self._locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()
        guild = discord.Object(id=settings.guild_id)

        @self.tree.command(
            name="verification-panel",
            guild=guild,
            description="Post the member verification panel (officers only).",
        )
        @app_commands.default_permissions(manage_guild=True)
        async def panel(interaction: discord.Interaction[Any]) -> None:
            if not await self.authorize(interaction, officer=True):
                return

            async def post() -> str:
                server, _ = self.role_context()
                channel = server.get_channel(self.settings.channel_id)
                if not isinstance(channel, discord.TextChannel):
                    raise VerificationError("Configure a server text channel for verification.")
                domains = ", ".join(f"@{domain}" for domain in self.settings.domains)
                await channel.send(
                    "**Welcome to Assistive Tech!**\n"
                    f"Verify your university email ({domains}) to access member channels.\n"
                    "1. Choose **Start verification** and enter your name and email.\n"
                    "2. Check your inbox, then choose **Enter code**. Codes last 10 minutes.\n"
                    "Use **Start verification** again to resend after the one-minute cooldown.\n"
                    "Already verified, rejoined, or missing your role? Choose **Retry access**.\n"
                    "Your name and email are stored privately for club verification. "
                    "Officers can access these records. Use /verification-forget to remove "
                    "your record and verified access. Contact an officer for help or exceptions.",
                    view=PanelView(self),
                )
                return "Verification panel posted."

            await self.execute(interaction, post)

        @self.tree.command(
            name="verification-reset",
            guild=guild,
            description="Remove a member's verified role and record (officers only).",
        )
        @app_commands.default_permissions(manage_guild=True)
        async def reset(
            interaction: discord.Interaction[Any],
            member: discord.Member,
        ) -> None:
            if not await self.authorize(interaction, officer=True):
                return

            async def remove() -> str:
                await self.remove_access(member.id)
                return "Verified access and stored record removed. Short-lived abuse limits remain."

            await self.execute(interaction, remove, user_id=member.id)

        @self.tree.command(
            name="verification-forget",
            guild=guild,
            description="Delete your verification record and remove verified access.",
        )
        async def forget(interaction: discord.Interaction[Any]) -> None:
            async def remove() -> str:
                await self.remove_access(interaction.user.id)
                return "Your verified access and record were removed. You may verify again later."

            await self.execute(interaction, remove)

        @self.tree.error
        async def command_error(
            interaction: discord.Interaction[Any],
            error: app_commands.AppCommandError,
        ) -> None:
            await self.report_error(interaction, error)

    async def setup_hook(self) -> None:
        self.add_view(PanelView(self))
        await self.tree.sync(guild=discord.Object(id=self.settings.guild_id))
        self.cleanup.start()

    async def on_ready(self) -> None:
        try:
            self.role_context()
        except VerificationError as error:
            log.error("Configuration check: %s", error)
        else:
            log.info("Verification bot connected; role configuration passed.")

    async def on_error(self, event_method: str, *args: object, **kwargs: object) -> None:
        log.error("Discord event failed; payload and exception text suppressed for privacy.")

    @tasks.loop(minutes=1)
    async def cleanup(self) -> None:
        try:
            await self.service.purge()
        except Exception as error:
            log.error("State cleanup failed (%s); no private details logged.", type(error).__name__)

    async def close(self) -> None:
        self.cleanup.cancel()
        await super().close()

    async def authorize(
        self,
        interaction: discord.Interaction[Any],
        *,
        officer: bool = False,
    ) -> bool:
        if (
            interaction.guild_id != self.settings.guild_id
            or interaction.channel_id != self.settings.channel_id
            or not isinstance(interaction.user, discord.Member)
            or interaction.user.bot
        ):
            await interaction.response.send_message(
                "Use the verification channel in the configured server with a member account.",
                ephemeral=True,
            )
            return False
        if officer and not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "Only officers with Manage Server permission can use this command.",
                ephemeral=True,
            )
            return False
        return True

    async def execute(
        self,
        interaction: discord.Interaction[Any],
        operation: Callable[[], Awaitable[str]],
        *,
        user_id: int | None = None,
    ) -> None:
        if not await self.authorize(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        key = interaction.user.id if user_id is None else user_id
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        try:
            async with lock:
                message = await operation()
        except Exception as error:
            await self.report_error(interaction, error)
        else:
            await interaction.followup.send(message, ephemeral=True)

    async def report_error(
        self,
        interaction: discord.Interaction[Any],
        error: Exception,
    ) -> None:
        if isinstance(error, VerificationError):
            message = str(error)
        else:
            log.error("Operation failed (%s); no private details logged.", type(error).__name__)
            message = (
                "Something went wrong. If you already entered a valid code, use Retry access. "
                "Otherwise try again later or contact an officer."
            )
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)

    def role_context(self, *, for_grant: bool = True) -> tuple[discord.Guild, discord.Role]:
        guild = self.get_guild(self.settings.guild_id)
        if guild is None or guild.me is None:
            raise VerificationError(
                "The bot cannot find its configured server. Contact an officer."
            )
        role = guild.get_role(self.settings.role_id)
        if role is None or role.is_default() or role.managed:
            raise VerificationError(
                "The verified role is missing or unsuitable. Contact an officer."
            )
        if for_grant and role.permissions.value & ~SAFE_MEMBER_PERMISSIONS.value:
            raise VerificationError("The verified role must not grant moderation or admin powers.")
        if (
            not guild.me.guild_permissions.manage_roles
            or role >= guild.me.top_role
            or (for_grant and guild.me.guild_permissions.administrator)
        ):
            raise VerificationError(
                "Give the bot Manage Roles (not Administrator) and place its role above Verified."
            )
        return guild, role

    async def grant_access(self, user_id: int) -> str:
        record = await self.service.member(self.settings.guild_id, user_id)
        if record is None:
            raise VerificationError(
                "Verify your email first using Start verification and Enter code."
            )
        if record.revoked:
            raise VerificationError(
                "Verification was revoked or deletion is pending. Contact an officer."
            )
        guild, role = self.role_context()
        member = await guild.fetch_member(user_id)
        if role not in member.roles:
            await member.add_roles(role, reason="University email verification", atomic=True)
        await self.service.mark_role_granted(self.settings.guild_id, user_id)
        return "You are verified! Member channels are now available."

    async def remove_access(self, user_id: int) -> None:
        # Persist even when the guild/role is missing or the hierarchy is broken.
        await self.service.begin_revocation(self.settings.guild_id, user_id)
        guild, role = self.role_context(for_grant=False)
        member = await guild.fetch_member(user_id)
        if role in member.roles:
            await member.remove_roles(role, reason="Verification record removal", atomic=True)
        # Fail closed: retain proof if Discord could not revoke access.
        await self.service.forget(self.settings.guild_id, user_id)


class PanelView(discord.ui.View):
    def __init__(self, client: VerificationClient) -> None:
        super().__init__(timeout=None)
        self.client = client

    async def interaction_check(self, interaction: discord.Interaction[Any]) -> bool:
        return await self.client.authorize(interaction)

    @discord.ui.button(
        label="Start verification",
        style=discord.ButtonStyle.primary,
        custom_id="assistive-tech:verify:start:v1",
    )
    async def start(
        self,
        interaction: discord.Interaction[Any],
        button: discord.ui.Button[PanelView],
    ) -> None:
        await interaction.response.send_modal(RegistrationModal(self.client))

    @discord.ui.button(
        label="Enter code",
        style=discord.ButtonStyle.secondary,
        custom_id="assistive-tech:verify:code:v1",
    )
    async def code(
        self,
        interaction: discord.Interaction[Any],
        button: discord.ui.Button[PanelView],
    ) -> None:
        await interaction.response.send_modal(CodeModal(self.client))

    @discord.ui.button(
        label="Retry access",
        style=discord.ButtonStyle.secondary,
        custom_id="assistive-tech:verify:retry:v1",
    )
    async def retry(
        self,
        interaction: discord.Interaction[Any],
        button: discord.ui.Button[PanelView],
    ) -> None:
        await self.client.execute(
            interaction, lambda: self.client.grant_access(interaction.user.id)
        )

    async def on_error(
        self,
        interaction: discord.Interaction[Any],
        error: Exception,
        item: discord.ui.Item[discord.ui.View],
    ) -> None:
        await self.client.report_error(interaction, error)


class PrivateModal(discord.ui.Modal):
    def __init__(self, client: VerificationClient, title: str) -> None:
        super().__init__(title=title, timeout=300)
        self.client = client

    async def on_error(
        self,
        interaction: discord.Interaction[Any],
        error: Exception,
        item: discord.ui.Item[Any] | None = None,
    ) -> None:
        await self.client.report_error(interaction, error)


class RegistrationModal(PrivateModal):
    def __init__(self, client: VerificationClient) -> None:
        super().__init__(client, "Verify your university email")
        self.first_name: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            min_length=1, max_length=80
        )
        self.last_name: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            min_length=1, max_length=80
        )
        self.email: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="netid@ucsb.edu", max_length=254
        )
        self.add_item(discord.ui.Label(text="First name", component=self.first_name))
        self.add_item(discord.ui.Label(text="Last name", component=self.last_name))
        self.add_item(discord.ui.Label(text="University email", component=self.email))

    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        async def request() -> str:
            await self.client.service.request(
                self.client.settings.guild_id,
                interaction.user.id,
                str(interaction.user),
                self.first_name.value,
                self.last_name.value,
                self.email.value,
            )
            return (
                "If this address is eligible, a code email has been requested. "
                "Check your inbox and spam folder, then choose Enter code. "
                "Codes expire in 10 minutes. You can try again after one minute. "
                "If no email arrives, check your address or contact an officer."
            )

        await self.client.execute(interaction, request)


class CodeModal(PrivateModal):
    def __init__(self, client: VerificationClient) -> None:
        super().__init__(client, "Enter your verification code")
        self.code: discord.ui.TextInput[discord.ui.Modal] = discord.ui.TextInput(
            placeholder="Six-digit email code", min_length=6, max_length=6
        )
        self.add_item(discord.ui.Label(text="Verification code", component=self.code))

    async def on_submit(self, interaction: discord.Interaction[Any]) -> None:
        async def verify() -> str:
            await self.client.service.verify(
                self.client.settings.guild_id, interaction.user.id, self.code.value
            )
            return await self.client.grant_access(interaction.user.id)

        await self.client.execute(interaction, verify)
