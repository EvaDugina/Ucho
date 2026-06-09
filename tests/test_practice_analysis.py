from __future__ import annotations

from datetime import datetime

import pytest

from bot import session, userctx, worldview
from bot.practice_analysis import analyzer, taxonomy
from bot.practice_analysis.models import PracticeAnalysisResult, PracticeCandidate
from bot.practice_analysis.signals import build_signals
from bot.practice_analysis.validation import validate_candidates
from bot.sensation_analysis.models import SensationAnalysisResult
from bot.services import conversation_service
from bot.understanding_analysis.models import UnderstandingAnalysisResult
from bot.values_norms_analysis.models import ValuesNormsAnalysisResult


EXPECTED_04 = {
    "readiness": (
        "начать", "рискнуть", "защитить", "признаться", "уйти",
        "попросить помощи", "отказаться", "вмешаться", "потерпеть",
        "выдержать конфликт", "принять решение", "взять ответственность",
        "изменить курс", "завершить", "восстановиться после провала",
    ),
    "will": (
        "дисциплина", "выдержка", "слабость воли", "упорство",
        "самоконтроль", "срыв", "привычка", "отложенное удовольствие",
        "верность решению", "преодоление страха", "преодоление лени",
        "устойчивость к давлению", "восстановление режима", "волевой отказ",
    ),
    "lifestyle": (
        "быт", "работа", "отдых", "отношения", "одиночество",
        "режим", "хаос", "порядок", "потребление", "здоровье",
        "сон", "питание", "движение", "медиа", "деньги",
        "пространство", "социальный круг", "ритуалы", "обучение",
    ),
    "actions": (
        "выбор в конфликте", "помощь", "отказ", "забота", "месть",
        "уступка", "борьба", "бегство", "признание ошибки",
        "защита границы", "разрыв связи", "примирение", "просьба",
        "обещание", "нарушение обещания", "жертва", "сделка",
    ),
    "strategies": (
        "избегание", "контроль", "переговоры", "давление", "терпение",
        "ирония", "рационализация", "планирование", "импровизация",
        "поиск поддержки", "изоляция", "конфронтация", "подстройка",
        "уход в работу", "уход в фантазию", "минимизация риска",
    ),
    "consequences": (
        "цена выбора", "повторяющийся паттерн", "компромисс",
        "самообман", "победа", "поражение", "утрата", "приобретение",
        "укрепление связи", "разрушение связи", "рост", "деградация",
        "вина после действия", "облегчение",
    ),
}


def test_taxonomy_contains_full_practice_04_canon():
    got = {category.key: category.themes for category in taxonomy.CATEGORIES}
    assert got == EXPECTED_04
    assert sum(len(v) for v in got.values()) == 95


def test_validate_candidates_rejects_invalid_theme_quote_and_confidence_and_maps_type():
    answer = "я решил попросить помощи, а после этого стало легче, хотя режим снова сорвался"
    raw = [
        {
            "category": "readiness",
            "theme": "попросить помощи",
            "type": "pattern",
            "name": "Решение попросить помощи",
            "summary": "Человек готов перейти к просьбе о помощи.",
            "quote": "я решил попросить помощи",
            "confidence": 0.87,
        },
        {
            "category": "consequences",
            "theme": "облегчение",
            "type": "action",
            "name": "Облегчение после просьбы",
            "summary": "После просьбы человеку стало легче.",
            "quote": "после этого стало легче",
            "confidence": 0.9,
        },
        {
            "category": "readiness",
            "theme": "несуществующая тема",
            "name": "Мусор",
            "summary": "Не из канона.",
            "quote": "я решил попросить помощи",
            "confidence": 0.9,
        },
        {
            "category": "will",
            "theme": "срыв",
            "name": "Недословная цитата",
            "summary": "Цитата перефразирована и должна быть отброшена.",
            "quote": "у меня сорвался режим",
            "confidence": 0.9,
        },
        {
            "category": "will",
            "theme": "срыв",
            "name": "Слабая уверенность",
            "summary": "Уверенность ниже порога.",
            "quote": "режим снова сорвался",
            "confidence": 0.4,
        },
    ]

    candidates, dropped = validate_candidates(raw, answer)

    assert len(candidates) == 2
    assert dropped == 3
    assert candidates[0].theme == "попросить помощи"
    assert candidates[0].type == "action"
    assert candidates[1].theme == "облегчение"
    assert candidates[1].type == "pattern"


def test_validate_candidates_maps_all_category_types():
    answer = (
        "режим снова сорвался, каждый день ухожу в работу, "
        "я попросил помощи и начал планирование"
    )
    raw = [
        {
            "category": "will",
            "theme": "срыв",
            "type": "action",
            "name": "Срыв режима",
            "summary": "Человек описывает срыв волевого режима.",
            "quote": "режим снова сорвался",
            "confidence": 0.88,
        },
        {
            "category": "lifestyle",
            "theme": "работа",
            "type": "strategy",
            "name": "Ежедневный уход в работу",
            "summary": "Работа описана как повторяющаяся часть жизни.",
            "quote": "каждый день ухожу в работу",
            "confidence": 0.85,
        },
        {
            "category": "actions",
            "theme": "просьба",
            "type": "pattern",
            "name": "Просьба о помощи",
            "summary": "Человек совершает действие просьбы о помощи.",
            "quote": "я попросил помощи",
            "confidence": 0.82,
        },
        {
            "category": "strategies",
            "theme": "планирование",
            "type": "claim",
            "name": "Планирование как способ",
            "summary": "Человек переходит к планированию как стратегии.",
            "quote": "начал планирование",
            "confidence": 0.8,
        },
    ]

    candidates, dropped = validate_candidates(raw, answer)

    assert dropped == 0
    assert [c.type for c in candidates] == ["pattern", "pattern", "action", "strategy"]


def test_signals_are_hints_not_graph_observations():
    signals = build_signals(
        "каждый день планирую работу, но когда страшно избегаю конфликта; после этого стало легче",
        mood_vec={"quality": "собранность", "valence": 0.1},
        vad={"valence": 0.0, "arousal": 0.2, "dominance": 0.1, "n": 3},
        method_results={"dostoevsky": {"top_label": "neutral"}},
    )

    assert "lifestyle" in signals["marker_categories"]
    assert "strategies" in signals["marker_categories"]
    assert "consequences" in signals["marker_categories"]
    assert "worldview_observations" not in signals
    assert "candidates" not in signals


@pytest.mark.asyncio
async def test_analyze_practice_validates_mocked_api_payload(monkeypatch):
    async def fake_api(**kwargs):
        assert "readiness" in kwargs["taxonomy_context"]
        assert "local_signals" not in kwargs["answer"]
        return {
            "candidates": [
                {
                    "category": "readiness",
                    "theme": "попросить помощи",
                    "type": "action",
                    "name": "Решение попросить помощи",
                    "summary": "Человек готов перейти к просьбе о помощи.",
                    "quote": "я решил попросить помощи",
                    "confidence": 0.88,
                    "evidence_reason": "прямая формулировка решения и действия",
                },
                {
                    "category": "readiness",
                    "theme": "попросить помощи",
                    "name": "Недословное",
                    "summary": "Цитата не из ответа.",
                    "quote": "мне нужна помощь",
                    "confidence": 0.9,
                },
            ]
        }

    monkeypatch.setattr(analyzer, "analyze_practice_json", fake_api)

    result = await analyzer.analyze_practice(
        "я решил попросить помощи",
        question="Что ты готов сделать?",
        target={"area": "practice", "category": "readiness", "theme": "попросить помощи"},
    )

    assert result.raw_count == 2
    assert result.dropped_count == 1
    assert [c.name for c in result.candidates] == ["Решение попросить помощи"]


def test_append_report_writes_separate_analysis04_note(as_user):
    result = PracticeAnalysisResult(
        candidates=[
            PracticeCandidate(
                category="readiness",
                theme="попросить помощи",
                type="action",
                name="Решение попросить помощи",
                summary="Человек готов перейти к просьбе о помощи.",
                quote="я решил попросить помощи",
                confidence=0.88,
                evidence_reason="прямая формулировка решения и действия",
            )
        ],
        raw_count=1,
        dropped_count=0,
        signals={"marker_categories": {"readiness": ["решил"]}},
    )

    analyzer.append_report(10, 23, result)

    files = sorted((userctx.user_root() / "04_Практический уровень" / "analysis04").glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "Анализ 04_Практический уровень" in text
    assert "Q10" in text
    assert "Решение попросить помощи" in text


@pytest.mark.asyncio
async def test_probe_owner_merges_practice_candidates_into_apply_processed(as_user, monkeypatch):
    target = {"area": "practice", "category": "actions", "theme": "просьба"}
    session.start(mode="probe", target=target)
    session.set_question("Что ты сделал?", target=target, q_num=1)

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
            "reaction": "Ты наконец сделал действие, а не построил вокруг него храм объяснений.",
            "user_delta": {},
            "mask_frequency_draft": {},
        }

    async def fake_analyze_sensation(*args, **kwargs):
        return SensationAnalysisResult(candidates=[], raw_count=0, dropped_count=0)

    async def fake_analyze_understanding(*args, **kwargs):
        return UnderstandingAnalysisResult(candidates=[], raw_count=0, dropped_count=0)

    async def fake_analyze_values_norms(*args, **kwargs):
        return ValuesNormsAnalysisResult(candidates=[], raw_count=0, dropped_count=0)

    async def fake_analyze_practice(*args, **kwargs):
        return PracticeAnalysisResult(
            candidates=[
                PracticeCandidate(
                    category="actions",
                    theme="просьба",
                    type="action",
                    name="Просьба о помощи",
                    summary="Человек совершает практический поступок просьбы о помощи.",
                    quote="я попросил помощи",
                    confidence=0.91,
                    evidence_reason="прямое описание поступка",
                )
            ],
            raw_count=1,
            dropped_count=0,
        )

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
        "я попросил помощи, а после этого стало легче",
        message_id=780,
        at=datetime(2026, 6, 9, 12, 50),
        is_owner=True,
    )

    assert payload is not None
    atom_slug = worldview.resolve_slug("Просьба о помощи")
    atom = worldview.load_atom(atom_slug)
    assert atom is not None
    assert atom.area == "practice"
    assert atom.category == "actions"
    assert atom.theme == "просьба"
