"""Тесты разделения портрета и настроения по папке personality/ + миграция."""
from __future__ import annotations

from bot import about, mood_file, moods, userctx


def _fresh(uid: int) -> None:
    userctx.set_user(uid)
    userctx.user_root().mkdir(parents=True, exist_ok=True)


def test_paths_under_personality():
    userctx.set_user(50001)
    assert about.path().as_posix().endswith("personality/about.md")
    assert mood_file.path().as_posix().endswith("personality/mood.md")


def test_fresh_ensure_creates_both_without_mood_in_about():
    _fresh(50002)
    about.ensure()
    mood_file.ensure()
    assert about.path().exists() and mood_file.path().exists()
    # настроенческих полей в about больше нет
    assert "mood_baseline" not in about.path().read_text(encoding="utf-8")


def test_legacy_about_user_migrates():
    _fresh(50003)
    legacy = userctx.user_root() / "about_user.md"
    legacy.write_text(
        "---\nregister: на ты\ntone: колкий\n"
        'mood_baseline: "-0.3,-0.2,-0.1"\nbot_mood: ласка\n---\n\n'
        "# Портрет пользователя\n\n## Характер\nзамкнут, ироничен\n",
        encoding="utf-8",
    )
    about.ensure()
    mood_file.ensure()
    ab = about.path().read_text(encoding="utf-8")
    md = mood_file.path().read_text(encoding="utf-8")
    # портрет перенесён, mood-полей в about нет
    assert "register: на ты" in ab and "замкнут" in ab
    assert "mood_baseline" not in ab
    # mood-поля уехали в mood.md
    assert "-0.3,-0.2,-0.1" in md and "ласка" in md
    assert mood_file.baseline() == (-0.3, -0.2, -0.1)
    # старый файл НЕ удалён (инвариант)
    assert legacy.exists()


def test_set_current_writes_and_preserves_baseline():
    _fresh(50004)
    legacy = userctx.user_root() / "about_user.md"
    legacy.write_text('---\nmood_baseline: "0.2,0.1,-0.1"\n---\n\n# x\n', encoding="utf-8")
    mood_file.ensure()
    assert mood_file.baseline() == (0.2, 0.1, -0.1)
    mv = moods.session_mood(
        [{"sign": "-", "energy": "low", "dominance": "low", "quality": "грусть_тоска"}]
    )
    mood_file.set_current(mv, "вселение_уверенности")
    # снимок записан, prior сохранён
    assert mood_file.baseline() == (0.2, 0.1, -0.1)
    assert "вселение_уверенности" in mood_file.render_for_prompt()
