import sqlite3
from contextlib import closing

import pytest

from discord_verification.storage import Storage


def test_future_schema_refused(tmp_path):
    path = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="Unsupported database"):
        Storage(path)


def test_symlink_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("untouched")
    link = tmp_path / "link.sqlite3"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="symbolic link"):
        Storage(link)
    assert target.read_text() == "untouched"
