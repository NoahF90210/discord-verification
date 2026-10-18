# Security and operations

## Scope of assurance

The bot checks inbox control for an exact configured domain, not university enrollment or real-world identity. Names are self-reported. Discord accounts can be compromised and verified members can still spam. Keep normal moderation, reporting, and an officer support path.

The default allowlist is `ucsb.edu` and `umail.ucsb.edu`, as explicitly confirmed by the project owner. No wildcard matching. The known pair's matching local parts are canonicalized to `local@ucsb.edu` for uniqueness. Local parts are case-normalized; plus-addressed variants are rejected. Additional aliases or domains require an explicit policy/code decision.

## Secrets and storage

- Use a cryptographically random `VERIFICATION_SECRET` of at least 32 bytes, stable across restarts. Protect it independently of source code and include it in secure operator handoff.
- Code verifiers and canonical email keys use HMAC-SHA256 with distinct context prefixes. A database-only leak does not make six-digit code hashes cheaply guessable without the secret.
- A secret fingerprint binds the database to its original secret, failing closed on accidental rotation rather than silently weakening duplicate-email enforcement.
- The database stores names, submitted email, user/server IDs, username snapshot, timestamps, and role state in plaintext. Filesystem permissions are not encryption; use device encryption and authorized operator access.
- New database directories are created with mode 0700 and the database with 0600 on Unix. Existing parent directories and backups must be protected by the operator. Windows requires equivalent account/ACL protection.
- Pending challenge PII is removed after expiry by a minute-based cleanup while the bot runs. Stopped bots do not clean up until next run. SQLite secure-delete is enabled, but this does not erase filesystem snapshots, backups, or SSD history.
- Member records are retained until `/verification-forget`, officer reset, or authorized operator deletion. Decide retention, alumni access, and backup deletion before adoption. No automatic enrollment checks or scheduled member re-verification.
- Request counters contain user/server IDs, HMAC email keys, and timestamps, but no plaintext names/emails/codes. They remain for a rolling hour, even after member deletion, to prevent deletion-based spam bypass. Cleanup removes expired counters.

The local SMTP demo contains codes temporarily in captured in-memory email messages, just as a real inbox would. It does not log or persist those codes. Production code must never enable SMTP debug logging or add raw interaction/exception logging.

## Abuse controls

Each request is bound to the authenticated Discord interaction's user and server, with a random challenge ID included in the code verifier. Successful use consumes the challenge transactionally. Wrong-format guesses count toward the attempt limit. Rate limits and pending state survive restarts.

Limits: 10-minute lifetime, 5 guesses, 60-second submission cooldown per user and send cooldown per canonical email, 5 submissions per user per rolling hour, 5 send attempts per canonical email, 100 send attempts per server, and 500 total submissions per server per hour. User submission throttles run before address-status lookups and count suppressed/duplicate submissions. Delivery failures count against send limits. Resending replaces the previous challenge; a deliverable challenge becomes usable only after SMTP submission succeeds. SMTP acceptance is not proof of final inbox delivery.

Requests use the same conditional acknowledgement for eligible, already-claimed, pending, or email-throttled addresses. Suppressed requests retain a short-lived non-verifiable decoy challenge so code-entry errors do not reveal address status. An eligibility flag prevents accepting even the decoy's generated code. SMTP delivery failures similarly invalidate eligibility and log only a safe category rather than exposing address-dependent errors to the requester. Response latency is not constant-time: SMTP/network timing can still differ, so this is not a guarantee against all timing inference. Persisted submission limits bound probing.

A malicious member can temporarily reserve another person's university email by requesting a code for it. Without inbox access they cannot verify it; the reservation expires after 10 minutes. Rate limits reduce abuse but do not eliminate targeted denial of service or coordinated account attacks. Officers can reset a pending request; retained abuse counters may still require waiting. There is no CAPTCHA or email-account ownership check before the first send.

## Role and moderation boundaries

Channel permissions are administered manually; the bot does not automatically secure the server. Audit all alternate roles and overrides. Do not grant the bot Administrator. The bot only grants its configured, non-managed, non-default member role. Its base permissions must be a subset of an explicit ordinary-member allowlist; moderation/management, mass-mention, elevated visibility, and unknown future permission bits are rejected. This base-role check does not replace auditing channel-specific overwrites.

Runtime server/channel/member and Manage Server checks accompany officer commands; default Discord command permissions alone are not treated as authorization.

Email proof is persisted before the Discord API call, so a grant outage can be retried after restart. A retried grant is idempotent. **Retry access restores the role from stored proof even if an officer manually removed that role. Use `/verification-reset` to revoke proof.** Reset/forget first persist a revoked marker and invalidate pending challenges, before checking Discord configuration, then remove the role, then delete the record. Removal does not apply the grant-only permission allowlist, so a mistakenly privileged role can still be removed. If role removal or final deletion fails, the retained record stays revoked: Retry access cannot restore it. An officer/operator must retry the reset/deletion to finish cleanup. Discord and SQLite are not a single atomic transaction; an API outage can delay actual role removal.

Run one process per server/database: in-process per-account locks serialize code/grant/reset/delete operations; SQLite transactions protect request, guess, and uniqueness operations. Multiple bot processes sharing the same database are not supported for role mutation.

## Mail and logs

Production mail always uses certificate-verified STARTTLS or implicit TLS before authentication. No plaintext fallback, self-signed bypass, OAuth adapter, incoming mail access, or email sending API is implemented. A separately labeled demo mailer allows unencrypted SMTP only to literal `127.0.0.1`.

User-facing errors and application logs avoid raw SMTP and Discord exception payloads. Do not capture private forms, mailbox codes, `.env`, database contents, or provider credentials in issues, screenshots, CI artifacts, or chat. Git ignore rules reduce mistakes but do not replace a secret review before committing.

## Recovery

Back up the database and matching secret securely while the bot is stopped. The current schema is version 2. Startup automatically migrates pre-release v1 databases transactionally, preserving member proof, pending codes/attempts, secret fingerprints, and request history. Old send records remain counted as send attempts; the old unconditional pending-email uniqueness constraint is replaced with a deliverable-only index. Migration failure rolls back the entire upgrade. Unknown future versions are refused rather than reset. Do not downgrade old code against a migrated database; restore the matching pre-upgrade backup for rollback. Do not rotate the secret casually or discard the database to clear rate limits. Losing member proof requires a deliberate re-verification plan; role membership is not automatically revoked if the database is lost. Restart with the original configuration after rollback.

No hosting, deployment, production account creation, automatic merger, public exports, or analytics service is included. Review code and complete the live acceptance checklist before adopting it for real members.
