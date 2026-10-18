from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import discord
import pytest
from conftest import request

from discord_verification.bot import CodeModal, PanelView, RegistrationModal, VerificationClient
from discord_verification.errors import VerificationError


@pytest.fixture
async def client(settings, service):
    bot = VerificationClient(settings, service)
    yield bot
    await bot.close()


def interaction(user_id=10, *, officer=False, guild_id=1, channel_id=2, bot=False):
    user = MagicMock(spec=discord.Member)
    user.id, user.bot = user_id, bot
    user.guild_permissions = discord.Permissions(manage_guild=officer)
    response = SimpleNamespace(
        defer=AsyncMock(),
        send_message=AsyncMock(),
        send_modal=AsyncMock(),
        is_done=Mock(return_value=True),
    )
    return SimpleNamespace(
        user=user,
        guild_id=guild_id,
        channel_id=channel_id,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.fixture
def guild(client):
    role = MagicMock(spec=discord.Role)
    role.is_default.return_value, role.managed = False, False
    role.permissions = discord.Permissions.none()
    role.__ge__.return_value = False
    member = MagicMock(spec=discord.Member)
    member.id, member.roles = 10, []
    member.add_roles, member.remove_roles = AsyncMock(), AsyncMock()
    channel = MagicMock(spec=discord.TextChannel)
    channel.send = AsyncMock()
    server = MagicMock(spec=discord.Guild)
    server.get_role.return_value = role
    server.get_channel.return_value = channel
    server.fetch_member = AsyncMock(return_value=member)
    server.me.guild_permissions = discord.Permissions(manage_roles=True)
    client.get_guild = Mock(return_value=server)
    return SimpleNamespace(server=server, role=role, member=member, channel=channel)


async def test_persistent_buttons_and_labeled_private_forms(client):
    view = PanelView(client)
    assert view.is_persistent()
    assert [button.label for button in view.children] == [
        "Start verification",
        "Enter code",
        "Retry access",
    ]
    assert len({button.custom_id for button in view.children}) == 3
    registration = RegistrationModal(client)
    assert [label.text for label in registration.children] == [
        "First name",
        "Last name",
        "University email",
    ]
    assert registration.email.max_length == 254
    assert CodeModal(client).code.max_length == 6
    assert not client.intents.message_content and not client.intents.members


async def test_setup_registers_persistent_view_and_guild_commands(client, monkeypatch):
    monkeypatch.setattr(client.tree, "sync", AsyncMock())
    monkeypatch.setattr(client.cleanup, "start", Mock())
    await client.setup_hook()
    assert len(client.persistent_views) == 1
    client.tree.sync.assert_awaited_once_with(guild=discord.Object(id=1))
    commands = client.tree.get_commands(guild=discord.Object(id=1))
    assert {command.name for command in commands} == {
        "verification-panel",
        "verification-reset",
        "verification-forget",
    }


@pytest.mark.parametrize(
    "kwargs",
    [
        {"guild_id": 99},
        {"guild_id": None},
        {"channel_id": 99},
        {"bot": True},
    ],
)
async def test_wrong_context_is_rejected_privately(client, kwargs):
    event = interaction(**kwargs)
    assert not await client.authorize(event)
    assert event.response.send_message.await_args.kwargs["ephemeral"]


async def test_officer_permission_checked_at_runtime(client):
    event = interaction()
    assert not await client.authorize(event, officer=True)
    assert "Manage Server" in event.response.send_message.await_args.args[0]
    assert await client.authorize(interaction(officer=True), officer=True)


async def test_complete_discord_callbacks_with_mocked_api(client, guild, mailer):
    event = interaction()
    form = RegistrationModal(client)
    form.first_name._value, form.last_name._value = "Test", "Member"
    form.email._value = "member@umail.ucsb.edu"
    await form.on_submit(event)
    event.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    assert event.followup.send.await_args.kwargs["ephemeral"]
    assert "If this address is eligible" in event.followup.send.await_args.args[0]
    entry = CodeModal(client)
    entry.code._value = mailer.messages[-1][1]
    completed = interaction()
    await entry.on_submit(completed)
    guild.member.add_roles.assert_awaited_once_with(
        guild.role, reason="University email verification", atomic=True
    )
    assert completed.followup.send.await_args.kwargs["ephemeral"]
    assert (await client.service.member(1, 10)).role_granted


async def test_grant_failure_keeps_proof_and_retry_recovers(client, guild, mailer):
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    guild.member.add_roles.side_effect = RuntimeError("synthetic API outage")
    with pytest.raises(RuntimeError):
        await client.grant_access(10)
    assert not (await client.service.member(1, 10)).role_granted
    guild.member.add_roles.side_effect = None
    assert "verified" in await client.grant_access(10)
    assert (await client.service.member(1, 10)).role_granted


async def test_retry_without_verification_is_denied(client, guild):
    with pytest.raises(VerificationError, match="Verify your email first"):
        await client.grant_access(10)
    guild.member.add_roles.assert_not_awaited()


async def test_existing_role_grant_is_idempotent(client, guild, mailer):
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    guild.member.roles = [guild.role]
    await client.grant_access(10)
    guild.member.add_roles.assert_not_awaited()
    assert (await client.service.member(1, 10)).role_granted


@pytest.mark.parametrize(
    "permission",
    [
        "administrator",
        "manage_guild",
        "manage_roles",
        "manage_channels",
        "manage_webhooks",
        "ban_members",
        "kick_members",
        "moderate_members",
        "manage_messages",
        "manage_threads",
        "manage_nicknames",
        "manage_expressions",
        "mute_members",
        "deafen_members",
        "move_members",
        "view_audit_log",
    ],
)
async def test_dangerous_verified_role_rejected(client, guild, permission):
    guild.role.permissions = discord.Permissions(**{permission: True})
    with pytest.raises(VerificationError, match="moderation or admin"):
        client.role_context()


async def test_hierarchy_missing_role_and_bot_admin_rejected(client, guild):
    guild.role.__ge__.return_value = True
    with pytest.raises(VerificationError, match="above Verified"):
        client.role_context()
    guild.role.__ge__.return_value = False
    guild.server.me.guild_permissions.administrator = True
    with pytest.raises(VerificationError, match="not Administrator"):
        client.role_context()
    guild.server.me.guild_permissions.administrator = False
    guild.server.get_role.return_value = None
    with pytest.raises(VerificationError, match="missing"):
        client.role_context()


async def test_remove_failure_retains_record(client, guild, mailer):
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    guild.member.roles = [guild.role]
    guild.member.remove_roles.side_effect = RuntimeError("synthetic outage")
    with pytest.raises(RuntimeError):
        await client.remove_access(10)
    assert await client.service.member(1, 10) is not None
    guild.member.remove_roles.side_effect = None
    await client.remove_access(10)
    assert await client.service.member(1, 10) is None


async def test_errors_do_not_leak_private_exception_text(client, caplog):
    event = interaction()
    await client.report_error(event, RuntimeError("PRIVATE-NAME private@ucsb.edu 123456 password"))
    response = event.followup.send.await_args.args[0]
    assert "PRIVATE-NAME" not in response + caplog.text
    assert "private@ucsb.edu" not in response + caplog.text
    assert "123456" not in response + caplog.text
    assert event.followup.send.await_args.kwargs["ephemeral"]


async def test_officer_panel_and_reset_callbacks(client, guild, mailer):
    panel = client.tree.get_command("verification-panel", guild=discord.Object(id=1))
    denied = interaction()
    await panel.callback(denied)
    guild.channel.send.assert_not_awaited()
    await panel.callback(interaction(officer=True))
    assert "@ucsb.edu, @umail.ucsb.edu" in guild.channel.send.await_args.args[0]
    assert isinstance(guild.channel.send.await_args.kwargs["view"], PanelView)
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    reset = client.tree.get_command("verification-reset", guild=discord.Object(id=1))
    await reset.callback(interaction(officer=True), guild.member)
    assert await client.service.member(1, 10) is None


async def test_self_forget_callback(client, guild, mailer):
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    command = client.tree.get_command("verification-forget", guild=discord.Object(id=1))
    await command.callback(interaction())
    assert await client.service.member(1, 10) is None


async def test_revocation_survives_api_failure_and_restart(client, guild, mailer, clock):
    from discord_verification.storage import Storage
    from discord_verification.verification import VerificationService

    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    guild.member.roles = [guild.role]
    guild.member.remove_roles.side_effect = RuntimeError("synthetic API outage")
    with pytest.raises(RuntimeError):
        await client.remove_access(10)
    assert (await client.service.member(1, 10)).revoked
    old_service = client.service
    client.service = VerificationService(
        Storage(old_service.storage.path), mailer, old_service.secret, clock=clock
    )
    with pytest.raises(VerificationError, match="revoked"):
        await client.grant_access(10)
    guild.member.add_roles.assert_not_awaited()
    guild.member.remove_roles.side_effect = None
    await client.remove_access(10)
    assert await client.service.member(1, 10) is None


async def test_revocation_survives_final_database_delete_failure(
    client, guild, mailer, monkeypatch
):
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    guild.member.roles = [guild.role]
    monkeypatch.setattr(
        client.service, "forget", AsyncMock(side_effect=OSError("synthetic DB error"))
    )
    with pytest.raises(OSError):
        await client.remove_access(10)
    guild.member.roles = []
    with pytest.raises(VerificationError, match="revoked"):
        await client.grant_access(10)
    guild.member.add_roles.assert_not_awaited()


async def test_reset_cancels_pending_challenge(client, guild, mailer):
    await request(client.service)
    code = mailer.messages[-1][1]
    await client.remove_access(10)
    with pytest.raises(VerificationError, match="No active"):
        await client.service.verify(1, 10, code)


@pytest.mark.parametrize("broken", ["permissions", "hierarchy", "missing_role", "missing_guild"])
async def test_reset_persists_revocation_before_configuration_failure(
    client,
    guild,
    mailer,
    clock,
    broken,
):
    from discord_verification.storage import Storage
    from discord_verification.verification import VerificationService

    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    if broken == "permissions":
        guild.server.me.guild_permissions.manage_roles = False
    elif broken == "hierarchy":
        guild.role.__ge__.return_value = True
    elif broken == "missing_role":
        guild.server.get_role.return_value = None
    else:
        client.get_guild.return_value = None
    with pytest.raises(VerificationError):
        await client.remove_access(10)
    assert (await client.service.member(1, 10)).revoked
    old = client.service
    client.service = VerificationService(Storage(old.storage.path), mailer, old.secret, clock=clock)
    guild.server.me.guild_permissions.manage_roles = True
    guild.role.__ge__.return_value = False
    guild.server.get_role.return_value = guild.role
    client.get_guild.return_value = guild.server
    with pytest.raises(VerificationError, match="revoked"):
        await client.grant_access(10)
    guild.member.add_roles.assert_not_awaited()


async def test_broken_configuration_still_cancels_pending_code(client, guild, mailer):
    await request(client.service)
    code = mailer.messages[-1][1]
    guild.server.me.guild_permissions.manage_roles = False
    with pytest.raises(VerificationError):
        await client.remove_access(10)
    with pytest.raises(VerificationError, match="No active"):
        await client.service.verify(1, 10, code)


@pytest.mark.parametrize("permission", ["manage_events", "pin_messages", "bypass_slowmode"])
async def test_additional_privileged_flags_rejected_but_removable(
    client, guild, mailer, permission
):
    guild.role.permissions = discord.Permissions(**{permission: True})
    with pytest.raises(VerificationError, match="moderation or admin"):
        client.role_context()
    await request(client.service)
    await client.service.verify(1, 10, mailer.messages[-1][1])
    guild.member.roles = [guild.role]
    await client.remove_access(10)
    guild.member.remove_roles.assert_awaited_once()
    assert await client.service.member(1, 10) is None


async def test_future_unknown_permission_bit_fails_closed(client, guild):
    guild.role.permissions = discord.Permissions(1 << 63)
    with pytest.raises(VerificationError, match="moderation or admin"):
        client.role_context()
    guild.role.permissions = discord.Permissions(view_channel=True, send_messages=True)
    assert client.role_context()[1] == guild.role
