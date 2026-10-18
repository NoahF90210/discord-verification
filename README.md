# Discord Verification

Email verification for the UCSB Assistive Tech Discord server.

**Status: planning and repository setup only. The bot is not implemented or ready for use.**

## Goal

Replace the manual Google Form approval process with a Discord bot that verifies access to an approved university email inbox and grants the server's verified-member role.

The original assignment is preserved in [IDEA.md](IDEA.md).

## Planned member experience

1. Join the server with access limited to welcome, verification, and support channels.
2. Click **Verify** and privately enter first name, last name, and university email.
3. Receive a one-time code by email.
4. Enter the code privately in Discord.
5. Receive the verified-member role and access the member channels.

Capture the Discord user ID automatically; do not ask members to type a username. Store the current username only as an officer-friendly reference, not as the account identifier.

## Email delivery: use an ordinary mailbox

The bot will send email programmatically through a club-controlled mailbox using authenticated SMTP over TLS. A dedicated transactional-email service such as Resend is not required. Mail still needs a provider to deliver it; the Python program is the sender client, not a self-hosted mail server.

- Use a club-owned sending account rather than a student's personal account.
- The provider must permit automated SMTP sending. Authentication may require an app-specific password or OAuth, depending on the provider and account policy.
- Check sending limits and delivery to actual student inboxes before adoption.
- Keep credentials in local environment configuration, never in GitHub.
- Buying an account or domain is not part of the current scope.

The sending mailbox does not need to be a UCSB address. The recipient's approved university address is what the bot verifies.

## Verification policy

- Approved recipient domain: **`ucsb.edu`**, as confirmed by the project owner.
- Match the parsed domain exactly, case-insensitively. Do not accept lookalike domains, suffix matches, or unapproved subdomains.
- Confirm representative member addresses before rollout. Additional domains require an explicit club policy decision; do not silently broaden the allowlist.
- Use short-lived, cryptographically generated, single-use codes. Store a protected verifier rather than plaintext codes and never log codes.
- Bind each challenge to the requesting Discord user, server, and email address.
- Limit guesses and send requests, enforce resend cooldowns, and invalidate superseded codes.
- Allow one verified Discord account per email within the server; let officers resolve transfers and exceptions.
- Persist state across restarts and make role assignment retryable when Discord temporarily fails.
- Grant only the designated role. Configure the bot with minimal permissions, not Administrator.

Email verification proves control of an approved inbox, not current enrollment, legal identity, or that a user cannot spam. Names are self-reported. Normal moderation and an officer support path remain necessary.

## Proposed implementation layout

These implementation files are planned, not present yet:

```text
src/discord_verification/
  bot.py            Discord buttons, forms, and role assignment
  verification.py   Challenge creation, validation, and limits
  mailer.py         SMTP delivery
  storage.py        SQLite member and challenge records
  config.py         Environment configuration and validation
tests/              Automated verification and failure-path tests
.env.example        Documented settings with placeholders only
pyproject.toml      Python dependencies and development commands
```

Keep one small Python application using `discord.py` and SQLite. No website, separate database server, or dashboard is planned.

## Member data and privacy

Planned records are server ID, Discord user ID, current username, submitted name, verified email, and verification timestamp. Pending challenges and abuse-control state are separate, short-lived records.

- Keep email addresses, names, and codes out of public Discord messages.
- Restrict database access to authorized club operators.
- Do not commit member databases, exports, logs, or mailbox credentials.
- Agree on retention, deletion, and officer access before real members use the bot.
- Back up retained records securely and document ownership transfer to future officers.

## Acceptance checks before club use

- Demonstrate a real email delivered to an approved inbox and a real role granted in a test Discord server.
- Confirm unverified users cannot access member channels and verified users can.
- Test invalid domains, malformed addresses, wrong and expired codes, replay, duplicate emails, and account/server binding.
- Test resend limits, guess limits, concurrent submissions, SMTP failure, and Discord role-permission failure.
- Restart the bot and verify persistence and safe recovery of incomplete role assignments.
- Verify private responses and usable keyboard/screen-reader interaction in Discord.
- Document installation, bot permissions and role hierarchy, configuration, running, recovery, and officer handoff.

Automated tests and simulated email delivery are useful but do not replace a real Discord-and-email demonstration.

## Still needed before implementation and live testing

- A Discord application/bot and an authorized test server.
- The target verification channel and verified-member role.
- A club-controlled SMTP-capable mailbox and its supported authentication method.
- Agreement on record retention and officer exceptions.

No hosting or deployment is included. A local machine can run the demonstration. Ongoing club use will require an operator to keep the bot running.

## GitHub basics

This repository is the shared project folder and change history. Commits are saved revisions; branches let changes be prepared without altering the main version; pull requests are where those changes can be reviewed before merging.

Repository access does not make it an appropriate place to store secrets or student records, even when the repository is private.
