from unittest.mock import AsyncMock, Mock

from discord_verification import __main__ as cli
from discord_verification import demo
from discord_verification.bot import VerificationClient
from discord_verification.config import Settings


def configure(monkeypatch, settings, *arguments):
    monkeypatch.setattr("sys.argv", ["discord-verification", *arguments])
    monkeypatch.setattr(cli, "load_dotenv", Mock())
    monkeypatch.setattr(Settings, "from_env", Mock(return_value=settings))


def test_check_does_not_connect(settings, monkeypatch, capsys):
    configure(monkeypatch, settings, "--check")
    run = Mock()
    monkeypatch.setattr(VerificationClient, "run", run)
    assert cli.main() == 0
    assert "No Discord or SMTP connection" in capsys.readouterr().out
    run.assert_not_called()


def test_run_wires_real_components_without_network(settings, monkeypatch):
    configure(monkeypatch, settings)
    run = Mock()
    monkeypatch.setattr(VerificationClient, "run", run)
    assert cli.main() == 0
    run.assert_called_once_with(settings.token, log_handler=None)
    assert settings.database.is_file()


def test_startup_exception_redacted(settings, monkeypatch, capsys):
    configure(monkeypatch, settings)
    monkeypatch.setattr(VerificationClient, "run", Mock(side_effect=RuntimeError("PRIVATE-TOKEN")))
    assert cli.main() == 1
    error = capsys.readouterr().err
    assert "RuntimeError" in error and "PRIVATE-TOKEN" not in error


def test_demo_path_needs_no_settings(monkeypatch):
    monkeypatch.setattr("sys.argv", ["discord-verification", "--demo"])
    run = AsyncMock()
    monkeypatch.setattr(demo, "run_demo", run)
    assert cli.main() == 0
    run.assert_awaited_once()


def test_missing_demo_dependency(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["discord-verification", "--demo"])
    monkeypatch.setattr(demo, "run_demo", AsyncMock(side_effect=ImportError()))
    assert cli.main() == 1
    assert "uv sync --locked" in capsys.readouterr().err
