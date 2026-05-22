"""Тесты разделения портрета и настроения по папке personality/."""
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


def test_set_current_writes_and_preserves_baseline():
    _fresh(50003)
    # depersonalization выставил baseline и нарратив в теле mood.md
    mp = mood_file.path()
    mp.parent.mkdir(parents=True, exist_ok=True)
    mp.write_text(
        "---\nupdated: '2026-05-23'\nmood_baseline: \"0.2,0.1,-0.1\"\nn: 0\n---\n\n"
        "# Настроение\n\n## Анализ настроения\n\nЧеловек всю неделю в апатии, гаснет к вечеру.\n",
        encoding="utf-8",
    )
    assert mood_file.baseline() == (0.2, 0.1, -0.1)

    mv = moods.session_mood(
        [{"sign": "-", "energy": "low", "dominance": "low", "quality": "грусть_тоска"}]
    )
    mood_file.set_current(mv, "вселение_уверенности")
    # снимок записан, prior сохранён, нарратив скилла НЕ затёрт
    assert mood_file.baseline() == (0.2, 0.1, -0.1)
    assert "вселение_уверенности" in mood_file.render_for_prompt()
    assert "апатии, гаснет к вечеру" in mp.read_text(encoding="utf-8")
