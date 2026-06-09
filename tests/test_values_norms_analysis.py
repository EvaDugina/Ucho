from __future__ import annotations

from datetime import datetime

import pytest

from bot import session, userctx, worldview
from bot.services import conversation_service
from bot.sensation_analysis.models import SensationAnalysisResult
from bot.understanding_analysis.models import UnderstandingAnalysisResult
from bot.values_norms_analysis import analyzer, taxonomy
from bot.values_norms_analysis.models import ValuesNormsAnalysisResult, ValuesNormsCandidate
from bot.values_norms_analysis.signals import build_signals
from bot.values_norms_analysis.validation import validate_candidates


EXPECTED_03 = {
    "values": (
        "свобода", "безопасность", "семья", "любовь", "справедливость",
        "достоинство", "развитие", "деньги", "власть", "здоровье",
        "творчество", "признание", "порядок", "истина", "красота",
        "верность", "независимость", "польза", "удовольствие",
        "милосердие",
    ),
    "ideals": (
        "идеальный человек", "идеальная жизнь", "идеальная любовь",
        "идеальная работа", "идеальное общество", "идеальная семья",
        "идеальная дружба", "идеальный лидер", "идеальный мастер",
        "идеальная вера", "идеальная свобода", "идеальная смерть",
        "идеальный поступок",
    ),
    "norms": (
        "честность", "вежливость", "долг", "верность", "уважение",
        "забота", "взаимность", "границы", "ответственность",
        "справедливость", "благодарность", "сдержанность", "ненасилие",
        "помощь слабому", "конфиденциальность", "обещание",
        "трудовая норма",
    ),
    "taboos": (
        "предательство", "унижение слабого", "трусость", "ложь",
        "насилие", "зависимость", "продажность", "подлость",
        "эксплуатация", "предательство себя", "предательство близких",
        "святотатство", "публичный позор", "паразитизм", "жестокость",
        "бесчестие",
    ),
    "hierarchy": (
        "свобода vs безопасность", "любовь vs долг",
        "правда vs милосердие", "деньги vs смысл",
        "семья vs призвание", "справедливость vs лояльность",
        "порядок vs спонтанность", "личное счастье vs ответственность",
        "власть vs достоинство", "удовольствие vs развитие",
        "традиция vs автономия",
    ),
    "judgement": (
        "вина", "заслуженность", "прощение", "наказание", "презрение",
        "восхищение", "требовательность", "снисхождение", "стыд",
        "гордость", "благодарность",
    ),
}


def test_taxonomy_contains_full_values_norms_03_canon():
    got = {category.key: category.themes for category in taxonomy.CATEGORIES}
    assert got == EXPECTED_03
    assert sum(len(v) for v in got.values()) == 88


def test_validate_candidates_rejects_invalid_theme_quote_and_confidence_and_maps_type():
    answer = "для меня честность важнее выгоды, а предательство близких я не прощаю"
    raw = [
        {
            "category": "norms",
            "theme": "честность",
            "type": "claim",
            "name": "Честность выше выгоды",
            "summary": "Человек принимает честность как норму, которая важнее выгоды.",
            "quote": "честность важнее выгоды",
            "confidence": 0.87,
        },
        {
            "category": "taboos",
            "theme": "предательство близких",
            "type": "value",
            "name": "Запрет предательства близких",
            "summary": "Предательство близких воспринимается как непростительное табу.",
            "quote": "предательство близких я не прощаю",
            "confidence": 0.9,
        },
        {
            "category": "norms",
            "theme": "несуществующая тема",
            "name": "Мусор",
            "summary": "Не из канона.",
            "quote": "честность важнее выгоды",
            "confidence": 0.9,
        },
        {
            "category": "values",
            "theme": "деньги",
            "name": "Недословная цитата",
            "summary": "Цитата перефразирована и должна быть отброшена.",
            "quote": "выгода для меня не главная",
            "confidence": 0.9,
        },
        {
            "category": "taboos",
            "theme": "предательство",
            "name": "Слабая уверенность",
            "summary": "Уверенность ниже порога.",
            "quote": "предательство близких",
            "confidence": 0.4,
        },
    ]

    candidates, dropped = validate_candidates(raw, answer)

    assert len(candidates) == 2
    assert dropped == 3
    assert candidates[0].theme == "честность"
    assert candidates[0].type == "norm"
    assert candidates[1].theme == "предательство близких"
    assert candidates[1].type == "taboo"


def test_validate_candidates_maps_hierarchy_and_judgement_types():
    answer = "свобода важнее безопасности, и я не прощаю себе трусость"
    raw = [
        {
            "category": "hierarchy",
            "theme": "свобода vs безопасность",
            "type": "claim",
            "name": "Свобода выше безопасности",
            "summary": "Человек ставит свободу выше безопасности.",
            "quote": "свобода важнее безопасности",
            "confidence": 0.88,
        },
        {
            "category": "judgement",
            "theme": "прощение",
            "type": "norm",
            "name": "Непрощение трусости",
            "summary": "Человек оценивает собственную трусость как непростительную.",
            "quote": "я не прощаю себе трусость",
            "confidence": 0.85,
        },
    ]

    candidates, dropped = validate_candidates(raw, answer)

    assert dropped == 0
    assert [c.type for c in candidates] == ["value", "claim"]


def test_signals_are_hints_not_graph_observations():
    signals = build_signals(
        "честность для меня важнее выгоды, а предательство близких недопустимо",
        mood_vec={"quality": "собранность", "valence": 0.1},
        vad={"valence": 0.0, "arousal": 0.2, "dominance": 0.1, "n": 3},
        method_results={"dostoevsky": {"top_label": "neutral"}},
    )

    assert "values" in signals["marker_categories"]
    assert "norms" in signals["marker_categories"]
    assert "taboos" in signals["marker_categories"]
    assert "hierarchy" in signals["marker_categories"]
    assert "worldview_observations" not in signals
    assert "candidates" not in signals


@pytest.mark.asyncio
async def test_analyze_values_norms_validates_mocked_api_payload(monkeypatch):
    async def fake_api(**kwargs):
        assert "norms" in kwargs["taxonomy_context"]
        assert "local_signals" not in kwargs["answer"]
        return {
            "candidates": [
                {
                    "category": "norms",
                    "theme": "честность",
                    "type": "norm",
                    "name": "Честность выше выгоды",
                    "summary": "Человек принимает честность как норму выше выгоды.",
                    "quote": "честность важнее выгоды",
                    "confidence": 0.88,
                    "evidence_reason": "прямое сравнение нормы и выгоды",
                },
                {
                    "category": "norms",
                    "theme": "честность",
                    "name": "Недословное",
                    "summary": "Цитата не из ответа.",
                    "quote": "мне важна честность",
                    "confidence": 0.9,
                },
            ]
        }

    monkeypatch.setattr(analyzer, "analyze_values_norms_json", fake_api)

    result = await analyzer.analyze_values_norms(
        "честность важнее выгоды",
        question="Что для тебя правильно?",
        target={"area": "values_norms", "category": "norms", "theme": "честность"},
    )

    assert result.raw_count == 2
    assert result.dropped_count == 1
    assert [c.name for c in result.candidates] == ["Честность выше выгоды"]


def test_append_report_writes_separate_analysis03_note(as_user):
    result = ValuesNormsAnalysisResult(
        candidates=[
            ValuesNormsCandidate(
                category="norms",
                theme="честность",
                type="norm",
                name="Честность выше выгоды",
                summary="Человек принимает честность как норму выше выгоды.",
                quote="честность важнее выгоды",
                confidence=0.88,
                evidence_reason="прямое сравнение нормы и выгоды",
            )
        ],
        raw_count=1,
        dropped_count=0,
        signals={"marker_categories": {"norms": ["честн"]}},
    )

    analyzer.append_report(9, 27, result)

    files = sorted((userctx.user_root() / "03_Ценностно-нормативная подсистема" / "analysis03").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Анализ 03_Ценностно-нормативная подсистема" in text
    assert "Q9" in text
    assert "Честность выше выгоды" in text


@pytest.mark.asyncio
async def test_probe_owner_merges_values_norms_candidates_into_apply_processed(as_user, monkeypatch):
    target = {"area": "values_norms", "category": "norms", "theme": "честность"}
    session.start(mode="probe", target=target)
    session.set_question("Что для тебя правильно?", target=target, q_num=1)

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
            "reaction": "Ты называешь это правилом, потому что боишься жить без меры.",
            "user_delta": {},
            "mask_frequency_draft": {},
        }

    async def fake_analyze_sensation(*args, **kwargs):
        return SensationAnalysisResult(candidates=[], raw_count=0, dropped_count=0)

    async def fake_analyze_understanding(*args, **kwargs):
        return UnderstandingAnalysisResult(candidates=[], raw_count=0, dropped_count=0)

    async def fake_analyze_values_norms(*args, **kwargs):
        return ValuesNormsAnalysisResult(
            candidates=[
                ValuesNormsCandidate(
                    category="norms",
                    theme="честность",
                    type="norm",
                    name="Честность выше выгоды",
                    summary="Человек принимает честность как норму выше выгоды.",
                    quote="честность важнее выгоды",
                    confidence=0.91,
                    evidence_reason="прямая формулировка приоритета нормы",
                )
            ],
            raw_count=1,
            dropped_count=0,
        )

    async def fake_analyze_practice(*args, **kwargs):
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
    monkeypatch.setattr(conversation_service, "analyze_practice", fake_analyze_practice)
    monkeypatch.setattr(conversation_service, "append_practice_report", lambda *args, **kwargs: None)

    payload = await conversation_service.process_probe_answer(
        "честность важнее выгоды, иначе легко предать себя",
        message_id=779,
        at=datetime(2026, 6, 9, 12, 40),
        is_owner=True,
    )

    assert payload is not None
    atom_slug = worldview.resolve_slug("Честность выше выгоды")
    atom = worldview.load_atom(atom_slug)
    assert atom is not None
    assert atom.area == "values_norms"
    assert atom.category == "norms"
    assert atom.theme == "честность"
