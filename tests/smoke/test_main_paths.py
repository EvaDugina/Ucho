from __future__ import annotations

from bot import books, session, session_log, userctx, vault


def test_clean_vault_smoke(as_user):
    vault.ensure_layout()
    current = session.start(domain="identity")
    session.set_question("Кто ты?", "identity", q_num=1)
    event = session_log.append_required(
        session_id=current.id,
        role="assistant",
        kind="question",
        text="Кто ты?",
        q_num=1,
        domain="identity",
        message_id=10,
    )
    assert event["telegram_message_id"] == 10
    assert session_log.find_session_by_message_id(10) == current.id
    assert books.list_books() == []
    root = userctx.user_root()
    assert sorted(path.name for path in root.iterdir() if path.is_dir()) == [
        "00_raw",
        "01_mood",
        "02_personality",
    ]
