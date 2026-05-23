"""Тесты разделения портрета и настроения по папке personality/."""
from __future__ import annotations

from bot import about, mood_file, moods, userctx

EXPECTED_ABOUT_SECTIONS = (
    "Манера речи",
    "Стиль",
    "Характер",
    "Эпистемический стиль",
    "Привязанность и дистанция",
    "Ритуалы и быт",
    "Self-image vs зазор",
    "Опоры самости",
    "Болевые точки",
    "Линии, которые не переходит",
    "Сквозные мотивы",
    "Отношение к власти и иерархии",
    "Корни и принадлежность",
    "Что значит дело",
    "Конечность и время",
    "Страсти (что вдохновляет)",
    "Огорчает / разочаровывает",
    "Общее",
    "Состояние диалога",
    "Эмоциональные реакции",
)


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
    assert not (userctx.user_root() / "about_user.md").exists()
    assert not (userctx.user_root() / "personality" / "softskills.md").exists()
    # настроенческих полей в about больше нет
    assert "mood_baseline" not in about.path().read_text(encoding="utf-8")


def test_about_skeleton_uses_canonical_20_sections():
    _fresh(50004)
    about.ensure()
    text = about.path().read_text(encoding="utf-8")

    assert tuple(about._SECTIONS) == EXPECTED_ABOUT_SECTIONS
    assert len(about._SECTIONS) == 20
    for section in EXPECTED_ABOUT_SECTIONS:
        assert f"## {section}\n" in text


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
