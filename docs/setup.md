# Setup and live-demo guide

## 1. Get the implementation

Use Python 3.12 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/).
Clone the repository with GitHub Desktop or Git. Because it is private, sign into GitHub with an account that has access.

```sh
git clone https://github.com/NoahF90210/discord-verification.git
cd discord-verification
```

Until the implementation pull request is merged, switch to its feature branch (`feat/email-verification`) before continuing. In GitHub Desktop use the Current Branch menu; in Git use `git switch feat/email-verification`.

```sh
uv sync --locked
uv run discord-verification --demo
```

The demo needs no Discord application or mailbox. It uses loopback SMTP and synthetic member data; the role grant is simulated. Test TLS certificate generation uses OpenSSL, available on the supported development/CI environments; install it if the test command reports it missing.

## 2. Create a Discord test application and server

1. Create a private test server you are authorized to administer.
2. Open the [Discord Developer Portal](https://discord.com/developers/applications), create an application, and open its Bot settings.
3. Obtain/reset its bot token and save it directly into your local `.env` later. Treat it as a password; do not paste it into chat, screenshots, GitHub, or an issue.
4. Leave **Message Content**, **Server Members**, and **Presence** privileged intents disabled. This application uses guild interactions and fetches individual members through HTTP when needed.
5. Set up **Guild Install** with scopes `bot` and `applications.commands`. Give the bot **View Channels**, **Send Messages**, and **Manage Roles**. Do not grant **Administrator**. Server policy may require additional channel overrides allowing the bot to see/send in the verification channel.
6. Install it only in your test server. No webhook, public web endpoint, or Interactions Endpoint URL is needed; the bot receives interactions over its Discord connection.

Reference: [Discord bot overview](https://docs.discord.com/developers/bots/overview).

## 3. Configure the role and channels

Create a normal **Verified Member** role, a verification text channel, and a support channel. Place the bot's role **above Verified Member** in the server's role hierarchy. Verified Member must not have administrative or moderation permissions; the bot rejects such roles.

For every member-only channel/category:

- Deny **View Channel** to `@everyone`.
- Allow **View Channel** and the intended member permissions to Verified Member.
- Audit other roles and channel-specific overrides: another role may otherwise bypass the gate.

For the verification channel:

- Allow `@everyone` to view it and use application commands.
- Allow the bot to view it and send messages.
- Keep it visible to verified members too, so Retry access and deletion commands remain available.
- You can deny ordinary members **Send Messages** to avoid email addresses being posted accidentally; test that buttons and application commands still work with the final permission configuration.

Let unverified members access a clearly identified officer-support channel. The bot **does not modify channel permissions for you**. Those permissions—not the bot merely being online—enforce restricted access.

Enable Developer Mode in Discord, then use Copy ID to collect the server, verification channel, and Verified Member role IDs.

## 4. Configure an SMTP mailbox

A normal SMTP-capable mailbox is enough. The sender address does not need to be a university address.

### iCloud example

You need an actual iCloud Mail mailbox, two-factor authentication, and an app-specific password. An alias/Hide My Email address is not a separate mailbox login.

1. At [account.apple.com](https://account.apple.com/), open **Sign-In and Security → App-Specific Passwords**.
2. Generate a password for this bot and store it only in local configuration.
3. Use these settings:

```dotenv
SMTP_HOST=smtp.mail.me.com
SMTP_PORT=587
SMTP_SECURITY=starttls
SMTP_USERNAME=replace_with_full_icloud_address
SMTP_PASSWORD=replace_with_app_specific_password
SMTP_FROM=replace_with_full_icloud_address
```

Do not substitute your primary Apple Account password. App-specific passwords are revoked if the primary account password changes; update the local configuration if that happens. Check mailbox limits, provider policy, spam placement, and actual UCSB delivery before club adoption.

Apple references: [SMTP settings](https://support.apple.com/en-us/102525) · [app-specific passwords](https://support.apple.com/en-us/102654).

For another provider, use its documented SMTP host, port, authentication and encryption. `SMTP_SECURITY` accepts `starttls` or `ssl` only. The current implementation supports password/app-password SMTP, not OAuth-only providers. It never falls back to plaintext or disables certificate checks.

## 5. Fill in local configuration

Copy the template:

```sh
cp .env.example .env
```

On Windows PowerShell, use `Copy-Item .env.example .env` instead.
Edit `.env` locally with the bot token, collected IDs, and SMTP details. Generate a stable random secret:

```sh
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Paste the result into `VERIFICATION_SECRET` locally, not chat or GitHub. Keep it stable across restarts. It protects code verifiers and canonical-email uniqueness. **Back it up securely with the database; a changed secret is rejected against an existing database.** Rotating this secret requires a deliberate migration, not simply editing `.env`.

Defaults:

- `ALLOWED_EMAIL_DOMAINS=ucsb.edu,umail.ucsb.edu` (exact domains, no wildcards).
- `DATABASE_PATH=data/verification.sqlite3` (relative to the directory from which you run the bot).
- Application code expiry/rate limits are the fixed defaults documented in the README.

For email, explicit `.env` entries for `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `SMTP_FROM` take precedence over inherited shell settings. This prevents stale credentials exported by another project from breaking the normal `uv run discord-verification` command. Missing entries fall back to the environment; environment-only setups still work without a `.env` file. Blank or placeholder local SMTP values fail validation rather than silently using old credentials. SMTP values are read literally, without `${VARIABLE}` expansion.

For all other settings (including the Discord token, server IDs, database path, and verification secret), existing environment variables still take precedence. Restart the bot after editing `.env`; a running process does not reload its settings. No `env -u` workaround is needed for SMTP settings saved in `.env`.

Always run from the project directory so the intended `.env` and database path are used. On Unix, restrict `.env` permissions with `chmod 600 .env`; protect the containing directory and any backups too.

## 6. Validate and start

```sh
uv run discord-verification --check
uv run discord-verification
```

`--check` validates configuration syntax only. It does not contact SMTP/Discord, check credentials, prove deliverability, or examine server permissions.

The normal run connects to Discord and registers commands in the configured server. Leave the terminal and computer running. The bot does not need the Discord desktop client to be open. Stopping the process stops new verification, but correctly configured channel permissions still restrict unverified members.

An officer with **Manage Server** runs `/verification-panel` **in the configured verification channel**. This posts three persistent buttons. Reposting creates another working panel; delete an older panel manually if desired. On restart, existing panels work without reposting.

## 7. Perform the real acceptance demonstration

Use a non-admin test member account; administrators bypass Discord channel restrictions.

- [ ] Before verification, the account can see verification/support but cannot see member-only channels.
- [ ] Both actual university-address forms needed by the club are accepted; confirm the alias policy against representative member accounts.
- [ ] A real code email arrives at the authorized university inbox; check spam and sender identity.
- [ ] Invalid domains, incorrect codes, expired codes, and code replay are rejected privately.
- [ ] The valid code grants only Verified Member and unlocks the intended channels.
- [ ] Restart the bot: existing buttons still work, and pending verification persists within expiry.
- [ ] Temporarily break the bot's role hierarchy: verification proof is retained, and after fixing hierarchy **Retry access** grants the role without another email.
- [ ] Officer `/verification-reset` removes access and proof; Retry access cannot bypass reset.
- [ ] `/verification-forget` removes access and the record.
- [ ] Verify names/emails/codes do not appear in public channels, logs, screenshots, or Git.
- [ ] Use keyboard-only navigation and a screen reader in Discord; verify labels, focus, errors, and success messages are usable.

Do not present local SMTP capture or mocked Discord tests as completion of these live checks.

## Troubleshooting and handoff

| Symptom | Check |
| --- | --- |
| Commands missing | Correct application/server; bot online; `applications.commands` scope; server/channel permission to use commands |
| Bot cannot grant a role | Correct role ID; bot has Manage Roles, not Administrator; bot role above Verified; target role has no moderation powers |
| No code email arrives | Check primary address, inbox/spam, whether that mailbox already verified another account, limits, and operator logs. Replies deliberately do not disclose address status. SMTP login/app password, provider restrictions, TLS pairing, or connectivity may also fail; failures count toward send limits |
| Email received but code unusable | Use the newest code; resend invalidates older codes; delivery failure may invalidate a code even if the provider later delivers it |
| Rate limit reached | Wait for the rolling hour; do not delete the database or restart to evade limits |
| Bot does not start after secret change | Restore the original secret with its matching database; do not bypass the fingerprint check |
| Channels accessible without verification | Audit `@everyone`, other roles, category sync, and channel overrides; test with a non-admin |
| Member removed from role can restore it | Use `/verification-reset` to revoke persisted proof, not manual role removal alone |

Run only **one bot process per configured server/database**. Transactions protect code consumption and reservations, but cross-process Discord role grant/reset operations are not a distributed job system.

For handoff: transfer the repository access, application ownership/authorized management, mailbox ownership, protected database/secret backups, and agreed privacy policy through secure channels. Replace your personal demo mailbox with the club's settings. Hosting and unattended operation are separate decisions, not included here.

For a deletion request from a departed member, the authorized operator should stop the bot, securely back up the database if policy permits, and delete that exact user/server's rows from `members` and `challenges` using a trusted SQLite tool. Do not put member records or backups in GitHub. There is no automatic alumni/offboarding sync.
