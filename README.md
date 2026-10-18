# Discord Verification

A small Python bot for the **UCSB Assistive Tech Discord server**. Members prove access to a university inbox, then receive a verified-member role automatically instead of waiting for Google Form approval.

**Implemented, with automated and local SMTP tests. Real mailbox delivery, live Discord permissions/role grants, and assistive-technology usability still require an authorized live test before club use.** No hosting is included.

## Member experience

1. Join the server and open its verification channel.
2. Choose **Start verification**, then privately enter first name, last name, and university email.
3. Check the email inbox and choose **Enter code** in Discord.
4. Submit the six-digit code to unlock member channels.

The bot captures the Discord user ID automatically. Emails, names, and codes are never posted publicly. **Retry access** recovers an interrupted role grant or restores access after rejoining.

Accepted domains are exactly **`ucsb.edu`** and **`umail.ucsb.edu`**, case-insensitively. Their matching local parts count as the same mailbox for duplicate-account checks. Other subdomains and lookalikes are rejected. Use a primary address without a `+alias`.

## Try the credential-free local demo

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then run from the implementation checkout:

```sh
uv sync --locked
uv run discord-verification --demo
```

If reviewing the unmerged implementation, check out the pull request's branch first. The default branch does not receive code until a human merges it.

The demo generates a real email, sends it to a **loopback-only SMTP server**, reads back the code, verifies a synthetic member, reopens the database, rejects an alias duplicate and code replay, and persists a **simulated** role grant. Nothing is sent to an external mailbox or Discord. Demo data is temporary and separate from the member database.

## Set up the real bot

See **[Setup and live-demo guide](docs/setup.md)** for the full walkthrough:

1. Create a Discord application and invite its bot to a test server.
2. Set up the Verified role and channel permissions.
3. Copy `.env.example` to a local `.env` and fill in the bot, server, role, and SMTP settings.
4. Validate and run:

```sh
uv run discord-verification --check
uv run discord-verification
```

An officer with **Manage Server** posts the persistent panel with `/verification-panel` in the configured channel. Keep this terminal running during the demonstration; GitHub stores code, it does not host the bot.

### Email delivery

Uses authenticated, certificate-verified **SMTP over STARTTLS or implicit TLS**. No Resend account, sending API, website, or custom domain is needed. Python's standard library sends through a normal mailbox. iCloud app-specific-password setup is documented in the guide.

The implementation supports SMTP username/password authentication, including provider-issued app passwords. Providers that require OAuth-only SMTP need an additional adapter; OAuth is not implemented. Prefer a club-owned mailbox for adoption; a personal test mailbox is sufficient for a demonstration if you accept that recipients see its sending address.

## Commands and recovery

| Control | Purpose |
| --- | --- |
| **Start verification** | Enter name/email, or request a replacement code after cooldown |
| **Enter code** | Submit the code privately |
| **Retry access** | Grant the configured role using this account's persisted verification proof |
| `/verification-panel` | Officer: post a verification panel |
| `/verification-reset member` | Officer: revoke proof and role, then delete the member record and pending challenge |
| `/verification-forget` | Member: revoke own verified access and delete own record/pending challenge |

**Officers must use `/verification-reset` to revoke verification**, not merely remove the role: persisted proof deliberately permits Retry access. Removing a role alone does not revoke that proof. Requests to erase a departed member's record can be handled by the authorized operator as described in the guide.

Manual guest exceptions remain a club moderation decision; use a separate guest role, not a fabricated email-verification record.

## Security and data

- Six-digit codes expire after **10 minutes** and allow **5 incorrect guesses** per challenge.
- Replacement requests invalidate old codes. Resends require **60 seconds** between sends.
- Persisted rolling-hour limits: **5 submissions per member**, **5 send attempts per canonical email**, **100 send attempts per server**, and **500 total submissions per server**. Duplicate/suppressed submissions consume the member's budget; SMTP failures consume send limits too. Restarting does not reset limits.
- Generic request and code-entry responses avoid disclosing another member's address status. Suppressed addresses cannot verify, even if their generated decoy code is guessed. No email is sent for them; contact an officer if a legitimate address does not receive a code.
- Codes are stored as keyed HMAC verifiers, not plaintext. Successful consumption and email uniqueness use SQLite transactions.
- Role grants are retryable after API failures and process restarts.
- Wrong server/channel, bot accounts, unsafe role hierarchy, and verified roles with permissions outside an explicit ordinary-member allowlist are rejected. Unknown future permission bits fail closed; channel overrides still require an operator audit.
- Neither Message Content nor Server Members privileged intents are needed. Do not grant the bot Administrator.
- Credentials, databases, exports, logs, and local artifacts are Git-ignored. Runtime errors avoid private exception payloads.

Records contain the server/user IDs, submitted names, username snapshot, verified email, timestamp, and role-grant state. They are private local data, **not encrypted database contents**. Pending challenges expire and are purged approximately every minute while the bot runs; hashed rate-limit records expire after an hour. Verified records remain until explicit deletion. The club must approve its retention/access policy before adoption. See [security and operations](docs/security.md).

Email verification proves control of an inbox—not legal identity, current enrollment, or immunity to spam. Normal moderation is still required.

## Repository

```text
src/discord_verification/
  __main__.py       CLI: run, validate configuration, or local demo
  bot.py            Persistent Discord buttons, private forms, roles, officer commands
  config.py         Environment settings and validation
  verification.py   Address rules, HMAC codes, async orchestration
  storage.py        Transactional SQLite records, throttles, and cleanup
  schema.py         Versioned schema and transactional v1-to-v2 migration
  mailer.py         TLS-only SMTP delivery, off the event loop
  errors.py         Safe messages suitable for members
  demo.py           Isolated loopback SMTP demonstration
tests/              Adversarial, persistence, concurrency, SMTP, and Discord-callback tests
docs/               Setup, operations, and verification notes
.github/workflows/  Test/lint/type/build checks only; no deployment
.env.example        Placeholder configuration
pyproject.toml      Dependencies and development tools
uv.lock             Reproducible dependency resolution
```

## Development checks

```sh
uv sync --locked
uv run pytest --cov=discord_verification --cov-report=term-missing
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv build
uv run discord-verification --demo
```

Tests use synthetic identities, mocked Discord APIs, real loopback SMTP, and a locally generated TLS certificate. They do **not** send verification emails to actual people. See [verification evidence and remaining live checks](docs/verification.md).

The original assignment is preserved unchanged in [IDEA.md](IDEA.md).
