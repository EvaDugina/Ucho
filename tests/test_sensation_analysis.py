from __future__ import annotations

from datetime import datetime

import pytest

from bot import session, userctx, worldview
from bot.services import conversation_service
from bot.sensation_analysis import analyzer, taxonomy
from bot.sensation_analysis.models import SensationAnalysisResult, SensationCandidate
from bot.sensation_analysis.signals import build_signals
from bot.sensation_analysis.validation import validate_candidates


EXPECTED_01 = {
    "emotions": (
        "радость", "интерес", "удивление", "надежда", "доверие",
        "нежность", "любовь", "благодарность", "спокойная удовлетворённость",
        "страх", "тревога", "стыд", "вина", "гнев", "раздражение",
        "отвращение", "печаль", "тоска", "зависть", "ревность",
        "обида", "презрение", "растерянность",
    ),
    "mood_background": (
        "апатия", "воодушевление", "внутреннее напряжение", "спокойствие",
        "тоска", "раздражение", "собранность", "подавленность",
        "лёгкость", "настороженность", "утомлённость",
        "эмоциональная пустота", "эмоциональная насыщенность", "скука",
        "предвкушение",
    ),
    "world_tone": (
        "мир добрый", "мир враждебный", "мир равнодушный", "мир хрупкий",
        "мир опасный", "мир щедрый", "мир абсурдный", "мир справедливый",
        "мир несправедливый", "мир живой", "мир механический",
        "мир загадочный", "мир испорченный", "мир открытый", "мир закрытый",
    ),
    "beauty_ugliness": (
        "красота", "уродство", "гармония", "дисгармония", "пошлость",
        "чистота", "грязь", "величие", "пустота", "уют", "холодность",
        "изящество", "грубость", "подлинность", "фальшь", "святость",
        "осквернение",
    ),
    "body_and_energy": (
        "усталость", "сила", "зажатость", "свобода тела", "бессилие",
        "азарт", "сонливость", "перегруз", "бодрость", "боль",
        "удовольствие", "телесная тревога", "телесная уверенность",
        "расслабление", "напряжение", "истощение", "возбуждение",
        "замедленность",
    ),
    "existential_feeling": (
        "одиночество", "принадлежность", "бездомность", "укоренённость",
        "конечность", "ожидание будущего", "смысловая полнота",
        "бессмысленность", "заброшенность", "призванность",
        "внутренний дом", "чуждость", "свобода существования",
        "обречённость", "благодарность за жизнь",
    ),
}


def test_taxonomy_contains_full_knowledge_01_canon():
    got = {category.key: category.themes for category in taxonomy.CATEGORIES}
    assert got == EXPECTED_01
    assert sum(len(v) for v in got.values()) == 103


def test_validate_candidates_rejects_invalid_theme_quote_and_confidence():
    answer = "мне стало страшно, и тревога в теле не отпускала"
    raw = [
        {
            "category": "emotions",
            "theme": "страх",
            "name": "Страх перед происходящим",
            "summary": "Человек прямо описывает страх в текущей ситуации.",
            "quote": "стало страшно",
            "confidence": 0.83,
        },
        {
            "category": "emotions",
            "theme": "несуществующая тема",
            "name": "Мусор",
            "summary": "Не из канона.",
            "quote": "стало страшно",
            "confidence": 0.9,
        },
        {
            "category": "body_and_energy",
            "theme": "телесная тревога",
            "name": "Недословная цитата",
            "summary": "Цитата перефразирована и должна быть отброшена.",
            "quote": "тело было тревожным",
            "confidence": 0.9,
        },
        {
            "category": "emotions",
            "theme": "тревога",
            "name": "Слабая уверенность",
            "summary": "Уверенность ниже порога.",
            "quote": "тревога в теле",
            "confidence": 0.4,
        },
    ]

    candidates, dropped = validate_candidates(raw, answer)

    assert len(candidates) == 1
    assert dropped == 3
    assert candidates[0].theme == "страх"


def test_signals_are_hints_not_graph_observations():
    signals = build_signals(
        "мир кажется опасным, тело устало и болит",
        mood_vec={"quality": "тревога", "valence": -0.5},
        vad={"valence": -0.4, "arousal": 0.2, "dominance": -0.3, "n": 3},
        method_results={"emolex": {"top": ["fear"], "fear": 0.5}},
    )

    assert "world_tone" in signals["marker_categories"]
    assert "body_and_energy" in signals["marker_categories"]
    assert "worldview_observations" not in signals
    assert "candidates" not in signals


@pytest.mark.asyncio
async def test_analyze_sensation_validates_mocked_api_payload(monkeypatch):
    async def fake_api(**kwargs):
        assert "emotions" in kwargs["taxonomy_context"]
        assert "local_signals" not in kwargs["answer"]
        return {
            "candidates": [
                {
                    "category": "emotions",
                    "theme": "страх",
                    "type": "feeling",
                    "name": "Страх перед темнотой",
                    "summary": "Темнота переживается как источник страха.",
                    "quote": "темнота меня пугает",
                    "confidence": 0.88,
                    "evidence_reason": "прямое называние страха",
                },
                {
                    "category": "emotions",
                    "theme": "страх",
                    "name": "Недословное",
                    "summary": "Цитата не из ответа.",
                    "quote": "я боюсь темноты",
                    "confidence": 0.9,
                },
            ]
        }

    monkeypatch.setattr(analyzer, "analyze_sensation_json", fake_api)

    result = await analyzer.analyze_sensation(
        "темнота меня пугает",
        question="Что ты чувствуешь ночью?",
        target={"area": "sensation", "category": "emotions", "theme": "страх"},
    )

    assert result.raw_count == 2
    assert result.dropped_count == 1
    assert [c.name for c in result.candidates] == ["Страх перед темнотой"]


def test_append_report_writes_separate_analysis01_note(as_user):
    result = SensationAnalysisResult(
        candidates=[
            SensationCandidate(
                category="emotions",
                theme="страх",
                name="Страх перед темнотой",
                summary="Темнота переживается как источник страха.",
                quote="темнота меня пугает",
                confidence=0.88,
                evidence_reason="прямое называние страха",
            )
        ],
        raw_count=1,
        dropped_count=0,
        signals={"marker_categories": {"emotions": ["пуга"]}},
    )

    analyzer.append_report(7, 20, result)

    files = sorted((userctx.user_root() / "01_Мироощущение" / "analysis01").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Анализ 01_Мироощущение" in text
    assert "Q7" in text
    assert "Страх перед темнотой" in text


@pytest.mark.asyncio
async def test_probe_owner_merges_sensation_candidates_into_apply_processed(as_user, monkeypatch):
    target = {"area": "values_norms", "category": "norms", "theme": "честность"}
    session.start(mode="probe", target=target)
    session.set_question("Что для тебя честность?", target=target, q_num=1)

    async def fake_score(text):
        return {"valence": -0.4, "arousal": 0.3, "dominance": -0.5, "n": 4}

    async def fake_classify_mood(*args, **kwargs):
        return {
            "sign": "-",
            "energy": "normal",
            "direction": "neutral",
            "quality": "тревога",
            "dominance": "low",
        }

    async def fake_run_all(*args, **kwargs):
        return {"pad": kwargs.get("mood_vec"), "emolex": None, "dostoevsky": None, "panas": None}

    async def fake_process_answer(**kwargs):
        return {
            "worldview_observations": [],
            "reaction": "Слышу, как тело говорит раньше тебя.",
            "user_delta": {},
            "mask_frequency_draft": {},
        }

    async def fake_analyze_sensation(*args, **kwargs):
        return SensationAnalysisResult(
            candidates=[
                SensationCandidate(
                    category="body_and_energy",
                    theme="телесная тревога",
                    name="Телесная тревога",
                    summary="Тревога переживается как телесное сжатие.",
                    quote="тревога в теле",
                    confidence=0.91,
                    evidence_reason="прямая телесная формулировка",
                )
            ],
            raw_count=1,
            dropped_count=0,
        )

    async def fake_analyze_understanding(*args, **kwargs):
        return None

    monkeypatch.setattr(conversation_service, "ANALYSIS_ENABLED", True)
    monkeypatch.setattr(conversation_service.lexicon, "score", fake_score)
    monkeypatch.setattr(conversation_service, "classify_mood", fake_classify_mood)
    monkeypatch.setattr(conversation_service.analysis, "run_all", fake_run_all)
    monkeypatch.setattr(conversation_service.analysis, "rebuild_chart", lambda: None)
    monkeypatch.setattr(conversation_service, "process_answer", fake_process_answer)
    monkeypatch.setattr(conversation_service, "analyze_sensation", fake_analyze_sensation)
    monkeypatch.setattr(conversation_service, "append_sensation_report", lambda *args, **kwargs: None)
    monkeypatch.setattr(conversation_service, "analyze_understanding", fake_analyze_understanding)
    monkeypatch.setattr(conversation_service, "append_understanding_report", lambda *args, **kwargs: None)

    payload = await conversation_service.process_probe_answer(
        "честность важна, но тревога в теле не отпускает",
        message_id=777,
        at=datetime(2026, 6, 9, 12, 0),
        is_owner=True,
    )

    assert payload is not None
    atom_slug = worldview.resolve_slug("Телесная тревога")
    atom = worldview.load_atom(atom_slug)
    assert atom is not None
    assert atom.area == "sensation"
    assert atom.category == "body_and_energy"
    assert atom.theme == "телесная тревога"
