from __future__ import annotations

import json

from bot import users


def test_malformed_registry_does_not_grant_access(tmp_path, monkeypatch):
    registry = tmp_path / "users.json"
    monkeypatch.setattr(users, "USERS_FILE", registry)

    registry.write_text('["not", "an", "object"]', encoding="utf-8")
    assert users.allowed_ids() == {users.OWNER_TELEGRAM_ID}

    registry.write_text(
        json.dumps({"users": [{"id": "../../bad"}, {"id": 4321}]}),
        encoding="utf-8",
    )
    assert users.allowed_ids() == {users.OWNER_TELEGRAM_ID, 4321}


def test_remove_user_revokes_env_seed_until_readded(tmp_path, monkeypatch):
    registry = tmp_path / "users.json"
    monkeypatch.setattr(users, "USERS_FILE", registry)
    monkeypatch.setattr(users, "ALLOWED_TELEGRAM_IDS", (4321,))

    assert users.is_allowed(4321)
    assert users.remove_user(4321) is True
    assert not users.is_allowed(4321)
    assert users.add_user(4321, by=users.OWNER_TELEGRAM_ID) is True
    assert users.is_allowed(4321)
