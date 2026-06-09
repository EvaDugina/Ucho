from __future__ import annotations

from datetime import datetime

import pytest

from bot import session, userctx, worldview
from bot.services import conversation_service
from bot.sensation_analysis.models import SensationAnalysisResult
from bot.understanding_analysis import analyzer, taxonomy
from bot.understanding_analysis.models import UnderstandingAnalysisResult, UnderstandingCandidate
from bot.understanding_analysis.signals import build_signals
from bot.understanding_analysis.validation import validate_candidates


EXPECTED_02 = {
    "knowledge": (
        "наука", "религия", "житейский опыт", "образование",
        "авторитеты", "традиция", "интуиция", "личный эксперимент",
    ),
    "beliefs": (
        "природа человека", "общество", "справедливость мира", "прогресс",
        "судьба", "свобода воли", "зло", "добро", "власть", "любовь",
        "труд", "успех", "страдание", "счастье", "смерть", "личность",
        "история", "будущее",
    ),
    "principles": (
        "не лгать себе", "проверять факты", "держать слово", "сомневаться",
        "искать причины", "не верить толпе", "отвечать за последствия",
        "не предавать близких", "выбирать меньшее зло", "уважать границы",
        "защищать слабого", "не унижаться", "сохранять достоинство",
        "не делать необратимого в аффекте",
    ),
    "causality": (
        "случайность", "закономерность", "личная ответственность", "система",
        "характер", "обстоятельства", "воля другого", "культура",
        "наследственность", "травма", "выбор", "привычка", "власть",
        "экономические условия", "духовная причина", "хаос",
    ),
    "self_world_model": (
        "кто я", "на что способен", "в чём ограничен", "что мной движет",
        "где моя роль", "моя сила", "моя слабость", "моя ответственность",
        "моя зависимость", "моя свобода", "мой долг", "моё место среди людей",
        "мой жизненный сценарий", "мои границы",
    ),
    "uncertainty": (
        "терпимость к неясности", "потребность в доказательствах",
        "вера без доказательств", "страх ошибки",
        "готовность пересматривать мнение", "стремление к контролю",
        "доверие вероятности", "принятие тайны", "избегание неизвестного",
        "исследовательский интерес", "паралич выбора", "импровизация",
    ),
}


def test_taxonomy_contains_full_knowledge_02_canon():
    got = {category.key: category.themes for category in taxonomy.CATEGORIES}
    assert got == EXPECTED_02
    assert sum(len(v) for v in got.values()) == 82


def test_validate_candidates_rejects_invalid_theme_quote_and_confidence():
    answer = "я проверяю факты, потому что без доказательств легко поверить в удобную ложь"
    raw = [
        {
            "category": "principles",
            "theme": "проверять факты",
            "name": "Проверка фактов",
            "summary": "Человек считает проверку фактов обязательным правилом мышления.",
            "quote": "я проверяю факты",
            "confidence": 0.84,
        },
        {
            "category": "principles",
            "theme": "несуществующая тема",
            "name": "Мусор",
            "summary": "Не из канона.",
            "quote": "я проверяю факты",
            "confidence": 0.9,
        },
        {
            "category": "uncertainty",
            "theme": "потребность в доказательствах",
            "name": "Недословная цитата",
            "summary": "Цитата перефразирована и должна быть отброшена.",
            "quote": "мне нужны доказательства",
            "confidence": 0.9,
        },
        {
            "category": "principles",
            "theme": "проверять факты",
            "name": "Слабая уверенность",
            "summary": "Уверенность ниже порога.",
            "quote": "проверяю факты",
            "confidence": 0.4,
        },
    ]

    candidates, dropped = validate_candidates(raw, answer)

    assert len(candidates) == 1
    assert dropped == 3
    assert candidates[0].theme == "проверять факты"
    assert candidates[0].type == "principle"


def test_signals_are_hints_not_graph_observations():
    signals = build_signals(
        "я считаю, что причины надо искать в системе, а факты проверять",
        mood_vec={"quality": "собранность", "valence": 0.1},
        vad={"valence": 0.0, "arousal": 0.2, "dominance": 0.1, "n": 3},
        method_results={"dostoevsky": {"top_label": "neutral"}},
    )

    assert "beliefs" in signals["marker_categories"]
    assert "causality" in signals["marker_categories"]
    assert "principles" in signals["marker_categories"]
    assert "worldview_observations" not in signals
    assert "candidates" not in signals


@pytest.mark.asyncio
async def test_analyze_understanding_validates_mocked_api_payload(monkeypatch):
    async def fake_api(**kwargs):
        assert "principles" in kwargs["taxonomy_context"]
        assert "local_signals" not in kwargs["answer"]
        return {
            "candidates": [
                {
                    "category": "principles",
                    "theme": "проверять факты",
                    "type": "principle",
                    "name": "Проверка фактов",
                    "summary": "Человек принимает проверку фактов как правило мышления.",
                    "quote": "я проверяю факты",
                    "confidence": 0.88,
                    "evidence_reason": "прямое называние правила проверки",
                },
                {
                    "category": "principles",
                    "theme": "проверять факты",
                    "name": "Недословное",
                    "summary": "Цитата не из ответа.",
                    "quote": "мне нужны факты",
                    "confidence": 0.9,
                },
            ]
        }

    monkeypatch.setattr(analyzer, "analyze_understanding_json", fake_api)

    result = await analyzer.analyze_understanding(
        "я проверяю факты",
        question="Чему ты доверяешь?",
        target={"area": "understanding", "category": "principles", "theme": "проверять факты"},
    )

    assert result.raw_count == 2
    assert result.dropped_count == 1
    assert [c.name for c in result.candidates] == ["Проверка фактов"]


def test_append_report_writes_separate_analysis02_note(as_user):
    result = UnderstandingAnalysisResult(
        candidates=[
            UnderstandingCandidate(
                category="principles",
                theme="проверять факты",
                type="principle",
                name="Проверка фактов",
                summary="Человек принимает проверку фактов как правило мышления.",
                quote="я проверяю факты",
                confidence=0.88,
                evidence_reason="прямое называние правила проверки",
            )
        ],
        raw_count=1,
        dropped_count=0,
        signals={"marker_categories": {"principles": ["факты"]}},
    )

    analyzer.append_report(8, 18, result)

    files = sorted((userctx.user_root() / "02_Миропонимание" / "analysis02").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Анализ 02_Миропонимание" in text
    assert "Q8" in text
    assert "Проверка фактов" in text


@pytest.mark.asyncio
async def test_probe_owner_merges_understanding_candidates_into_apply_processed(as_user, monkeypatch):
    target = {"area": "sensation", "category": "emotions", "theme": "тревога"}
    session.start(mode="probe", target=target)
    session.set_question("Что ты чувствуешь?", target=target, q_num=1)

    async def fake_score(text):
        return {"valence": 0.0, "arousal": 0.2, "dominance": 0.1, "n": 4}

    async def fake_classify_mood(*args, **kwargs):
        return {
            "sign": "0",
            "energy": "normal",
            "direction": "neutral",
            "quality": "собранность",
            "dominance": "normal",
        }

    async def fake_run_all(*args, **kwargs):
        return {"pad": kwargs.get("mood_vec"), "emolex": None, "dostoevsky": None, "panas": None}

    async def fake_process_answer(**kwargs):
        return {
            "worldview_observations": [],
            "reaction": "Ты сперва строишь суд, а потом называешь это спокойствием.",
            "user_delta": {},
            "mask_frequency_draft": {},
        }

    async def fake_analyze_sensation(*args, **kwargs):
        return SensationAnalysisResult(candidates=[], raw_count=0, dropped_count=0)

    async def fake_analyze_understanding(*args, **kwargs):
        return UnderstandingAnalysisResult(
            candidates=[
                UnderstandingCandidate(
                    category="principles",
                    theme="проверять факты",
                    type="principle",
                    name="Проверка фактов",
                    summary="Человек принимает проверку фактов как правило мышления.",
                    quote="я проверяю факты",
                    confidence=0.91,
                    evidence_reason="прямая формулировка правила проверки",
                )
            ],
            raw_count=1,
            dropped_count=0,
        )

    async def fake_analyze_values_norms(*args, **kwargs):
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
    monkeypatch.setattr(conversation_service, "analyze_values_norms", fake_analyze_values_norms)
    monkeypatch.setattr(conversation_service, "append_values_norms_report", lambda *args, **kwargs: None)

    payload = await conversation_service.process_probe_answer(
        "я проверяю факты, иначе легко соврать себе",
        message_id=778,
        at=datetime(2026, 6, 9, 12, 30),
        is_owner=True,
    )

    assert payload is not None
    atom_slug = worldview.resolve_slug("Проверка фактов")
    atom = worldview.load_atom(atom_slug)
    assert atom is not None
    assert atom.area == "understanding"
    assert atom.category == "principles"
    assert atom.theme == "проверять факты"
