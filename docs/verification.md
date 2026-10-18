# Verification evidence and live-test boundary

## Automated and local coverage

The suite exercises:

- Exact university domains, U-Mail alias uniqueness, case handling, lookalikes, control characters, malformed input, and primary-address rules.
- One-time consumption, expiration boundaries, wrong and malformed code attempts, account/server binding, resend invalidation, duplicate emails, and rolling send limits.
- Concurrent requests/consumption, persisted attempts and limits after restart, protected database permissions, secret-change rejection, cancellation/send failures, and pending versus completed role state.
- Production SMTP authentication after STARTTLS with a trusted local test certificate, implicit-TLS construction, missing-STARTTLS refusal without credential fallback, and redacted failures.
- Real loopback SMTP message content and verification flow using synthetic identities.
- Persistent labeled Discord components, private replies, runtime officer checks, wrong-server/channel/bot rejection, mocked registration/code callbacks, unsafe role rejection, failed role grants/retries, fail-closed revocation after API/delete errors, and reset/deletion behavior.
- Configuration validation and CLI preflight without network access.
- Transactional schema upgrades from both pre-release v1 layouts, pending-code/member/rate-limit preservation, decoy coexistence after upgrade, idempotent reopen, and full rollback on injected migration failure.
- Rejected-address submission limits before status lookups, indistinguishable request/code-entry error text for claimed/pending/unused addresses, decoy-code rejection, and SMTP error privacy.
- Revocation despite missing guild/role/permissions or broken hierarchy, recovery across restart, removal of accidentally privileged roles, and unknown permission bits failing closed.

`uv run discord-verification --demo` separately prints its real local SMTP/persistence checks and explicitly labels the role completion as simulated. Run it twice to check repeatability without reusing real member records.

GitHub checks run tests, lint, format, types, package build, and the loopback demo. They do not deploy or use mailbox/bot credentials.

## Not yet verified live

These require an authorized Discord test server, bot token, SMTP mailbox credentials, and university inbox:

- Authentication to the actual sending mailbox and delivery into a UCSB inbox (including spam placement and provider limits).
- Real Discord installation, command registration, persistent-button behavior after reconnect, role permissions/hierarchy, and channel gating.
- Keyboard and screen-reader usability in an actual Discord client; offline component-label inspection is not accessibility sign-off.
- Club-specific privacy notice, retention period, officer access, exceptions, and operational ownership.

Follow the checklist in [setup.md](setup.md). Passing tests is not a production-readiness claim. There is no claim of screenshot-based UI verification or live inbox/Discord evidence in this repository.
