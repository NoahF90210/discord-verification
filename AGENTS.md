# Project instructions

Small, single-process Python Discord bot. No website, dashboard, hosting, or paid service.
Use Python 3.12+, discord.py, SQLite, and TLS SMTP. Run `uv sync --locked` then
`uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`,
`uv run mypy`, `uv build`, and `uv run discord-verification --demo`.

Accept exact configured domains (default ucsb.edu and umail.ucsb.edu); never use substring
matching. Their known U-Mail alias shares a canonical identity. Keep codes, credentials,
and member data out of logs and Git. Preserve persisted throttles across restarts.
Use transactions for consumption and uniqueness. Do not block Discord's event loop on
SQLite or SMTP. No message-content or members privileged intent is required.

Tests use synthetic identities, loopback SMTP, and mocked Discord interactions. They do
not prove real inbox delivery, real role assignment, or assistive-technology usability.
Never claim those without a live authorized test. Do not deploy or merge automatically.
