"""Сравнение методов оценки настроения/состояния по сообщению (OWNER-тестирование).

Гоняет НЕСКОЛЬКО независимых методов на один ответ человека (в контексте сессии),
складывает их выводы в Markdown-отчёт `01_mood/analysis/YYYY-MM-DD.md` и пишет
числа рядом в durable-ряд `01_mood/timeseries/YYYY-MM.jsonl` — чтобы потом на практике
выбрать самые точные методы и строить графики колебаний настроения (день/неделя/
месяц/сезон/год). Заметку-график `01_mood/График настроения.md` рисует плагин Obsidian Charts.

Методы (провайдеры):
- **pad** — текущий пайплайн настроения (AITunnel-классификатор + код): в отчёт идёт эмоция.
- **emolex** — эмо-лексикон NRC-EmoLex, Плутчик-8 (`bot/emolex.py`).
- **dostoevsky** — тональность RuSentiment (`bot/sentiment_dvk.py`, graceful-optional).
- **panas** — кодовая оценка текущего позитивного/негативного аффекта по сигналам выше.

Принцип проекта сохранён: методы дают сигнал, арбитр-персона отвечает отдельно.
Любой сбой провайдера → None (не участвует), обработка ответа не падает.
"""
from __future__ import annotations

import asyncio
import json
import logging

from . import emolex, sentiment_dvk, userctx
from .atomic import atomic_write_text

log = logging.getLogger(__name__)

_CHART_DAYS = 180  # сколько последних дней показывать на графике
_CHART_NOTE = "График настроения.md"


def _ts_dir():
    return userctx.user_root() / "01_mood" / "timeseries"


def _analysis_dir():
    return userctx.user_root() / "01_mood" / "analysis"


async def run_all(
    text: str,
    history: list[dict] | None,
    *,
    mood_vec: dict | None,
    vad: dict | None,
    session_context: str = "",
) -> dict:
    """Запустить все методы конкурентно. `mood_vec`/`vad` уже посчитаны пайплайном
    настроения — переиспользуем, не дублируем вызовы. Возвращает {метод: результат|None}.
    """
    loop = asyncio.get_event_loop()
    emolex_fut = loop.run_in_executor(None, emolex.score_sync, text)
    dvk_fut = loop.run_in_executor(None, sentiment_dvk.score_sync, text)

    emolex_r, dvk_r = await asyncio.gather(
        emolex_fut, dvk_fut, return_exceptions=True,
    )

    def _ok(r):
        if isinstance(r, Exception):
            log.warning("analysis provider failed: %r", r)
            return None
        return r

    emolex_r = _ok(emolex_r)
    dvk_r = _ok(dvk_r)

    return {
        "pad": _pad_view(mood_vec),
        "emolex": emolex_r,
        "dostoevsky": dvk_r,
        "panas": _panas_from_signals(_pad_view(mood_vec), emolex_r, dvk_r),
    }


def _pad_view(mv: dict | None) -> dict | None:
    if not isinstance(mv, dict):
        return None
    return {
        "quality": mv.get("quality"),
        "valence": mv.get("valence"), "arousal": mv.get("arousal"),
        "dominance": mv.get("dominance"), "dominance_label": mv.get("dominance_label"),
        "stability": mv.get("stability"),
    }


# --- словари расшифровки (число → понятная фраза) ---
_EMO_RU = {
    "anger": "гнев", "anticipation": "предвкушение", "disgust": "отвращение",
    "fear": "страх", "joy": "радость", "sadness": "грусть", "surprise": "удивление",
    "trust": "доверие",
}
_DVK_RU = {
    "positive": "позитив", "negative": "негатив", "neutral": "нейтрально",
    "skip": "не определить", "speech": "речевой этикет",
}
_PANAS_RU = (
    ("positive_affect", "позитивный аффект"),
    ("negative_affect", "негативный аффект"),
)


def _num(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _clamp01(x: float) -> float:
    return round(max(0.0, min(1.0, x)), 3)


def _avg(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 3) if xs else None


def _panas_from_signals(pad: dict | None, emo: dict | None, dvk: dict | None) -> dict | None:
    """PANAS-like сигнал 0..1 из уже посчитанных методов, без OCEAN/психотипирования."""
    pos: list[float] = []
    neg: list[float] = []

    if isinstance(pad, dict):
        v = _num(pad.get("valence"))
        a = _num(pad.get("arousal"))
        if v is not None:
            arousal = (a + 1.0) / 2.0 if a is not None else 0.5
            pos.append(_clamp01(0.7 * ((v + 1.0) / 2.0) + 0.3 * arousal))
            neg.append(_clamp01(0.7 * ((-v + 1.0) / 2.0) + 0.3 * arousal))

    if isinstance(emo, dict):
        ep = _num(emo.get("positive"))
        en = _num(emo.get("negative"))
        if ep is not None:
            pos.append(_clamp01(ep))
        if en is not None:
            neg.append(_clamp01(en))

    if isinstance(dvk, dict):
        dp = _num(dvk.get("positive"))
        dn = _num(dvk.get("negative"))
        if dp is not None:
            pos.append(_clamp01(dp))
        if dn is not None:
            neg.append(_clamp01(dn))

    if not pos and not neg:
        return None
    return {"positive_affect": _avg(pos), "negative_affect": _avg(neg), "source": "code"}


def format_report(mood_vec: dict | None, bot_mood: str | None, results: dict) -> str:
    """Единый Markdown-отчёт сравнения методов.

    Только итоговые инструментальные сигналы (без слов человека) → можно безопасно
    писать в vault без сохранения дословного пользовательского текста.
    """
    L = ["🧪 Анализ ответа — методы"]

    pad = results.get("pad")
    L.append("\n▸ PAD (AITunnel+код)")
    if pad:
        L.append(f"эмоция: {pad.get('quality')}")
        L.append(f"выбранное лицо Иуды: {bot_mood or '—'}")
    else:
        L.append("нет данных")

    emo = results.get("emolex")
    L.append("\n▸ NRC-EmoLex (эмоции Плутчика, по словам)")
    if emo:
        top = ", ".join(f"{_EMO_RU.get(e, e)} {emo.get(e)}" for e in (emo.get("top") or [])) or "нет выраженных"
        L.append(f"ведущие эмоции: {top}")
        L.append(f"полярность: позитив {emo.get('positive')} / негатив {emo.get('negative')} — "
                 f"{'преобладает негатив' if (emo.get('negative') or 0) > (emo.get('positive') or 0) else ('преобладает позитив' if (emo.get('positive') or 0) > (emo.get('negative') or 0) else 'поровну')}")
    else:
        L.append("нет совпадений со словарём")

    dvk = results.get("dostoevsky")
    L.append("\n▸ Dostoevsky (тональность, RuSentiment)")
    if dvk:
        L.append(f"{_DVK_RU.get(dvk.get('label'), dvk.get('label'))}: {dvk.get('score')} — уверенность модели")
    else:
        L.append("нет данных (модель не загружена)")

    pa = results.get("panas")
    L.append("\n▸ PANAS (код, текущий аффект, 0..1)")
    if pa:
        for key, ru in _PANAS_RU:
            L.append(f"{ru}: {pa.get(key)}")
    else:
        L.append("нет данных")

    return "\n".join(L)


def append_report(q_num: int | None, text_len: int, report: str) -> None:
    """Дописать человекочитаемый отчёт методов в `01_mood/analysis/YYYY-MM-DD.md`.

    Это knowledge-base след для depersonalization/ручного чтения. В чат отчёт не
    отправляется: пользователю уходит только основная реакция Иуды.
    """
    try:
        from datetime import datetime
        now = datetime.now()
        d = _analysis_dir()
        p = d / f"{now:%Y-%m-%d}.md"
        if p.exists():
            body = p.read_text(encoding="utf-8").rstrip() + "\n\n"
        else:
            body = f"# Анализ методов · {now:%Y-%m-%d}\n\n"
        q_label = f"Q{q_num}" if q_num is not None else "Q?"
        body += f"## {now:%H:%M} · {q_label} · len={text_len}\n\n{report.strip()}\n"
        atomic_write_text(p, body)
    except Exception:
        log.exception("append_report failed (non-fatal)")


_METHOD_KEYS = ("pad", "emolex", "dostoevsky", "panas")


def append_point(text_len: int, results: dict) -> None:
    """Дописать точку временного ряда в `01_mood/timeseries/YYYY-MM.jsonl`.

    Durable append-only (НЕ кольцо, НЕ ротируется) — основа для графиков колебания
    настроения за день/неделю/месяц/сезон/год. Помесячная партиция держит файлы
    мелкими. Пишем выводы ВСЕХ методов (для сравнения трендов методов во времени).
    Слова человека НЕ сохраняем — только длину сообщения и числа.
    """
    try:
        from datetime import datetime
        now = datetime.now()
        entry = {
            "ts": now.isoformat(timespec="seconds"),
            "len": text_len,
            **{k: results.get(k) for k in _METHOD_KEYS},
        }
        d = _ts_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{now:%Y-%m}.jsonl"
        lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False))
        atomic_write_text(p, "\n".join(lines) + "\n")
    except Exception:
        log.exception("append_point failed (non-fatal)")


def _read_recent_points(days: int) -> list[dict]:
    """Прочитать точки ряда за последние `days` дней (по всем помесячным файлам)."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    out: list[dict] = []
    d = _ts_dir()
    if not d.exists():
        return out
    for f in sorted(d.glob("*.jsonl")):
        try:
            for line in f.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                pt = json.loads(line)
                if isinstance(pt, dict) and (pt.get("ts") or "") >= cutoff:
                    out.append(pt)
        except Exception:
            log.exception("read timeseries file failed: %s (non-fatal)", f)
    return out


def aggregate_daily(points: list[dict]) -> tuple[list[str], dict[str, list]]:
    """Дневное среднее PAD (valence/arousal/dominance) по точкам ряда. Чистая функция.

    Returns (labels=отсортированные даты YYYY-MM-DD, {valence:[...], arousal:[...],
    dominance:[...]}). Дни без PAD пропускаются.
    """
    from collections import defaultdict
    axes = ("valence", "arousal", "dominance")
    buckets: dict[str, dict[str, list]] = defaultdict(lambda: {a: [] for a in axes})
    for pt in points:
        pad = pt.get("pad")
        ts = pt.get("ts")
        if not isinstance(pad, dict) or not ts:
            continue
        day = ts[:10]
        for a in axes:
            v = pad.get(a)
            if isinstance(v, (int, float)):
                buckets[day][a].append(v)
    labels = sorted(buckets)
    series: dict[str, list] = {a: [] for a in axes}
    for day in labels:
        for a in axes:
            xs = buckets[day][a]
            series[a].append(round(sum(xs) / len(xs), 3) if xs else None)
    return labels, series


def rebuild_chart(days: int = _CHART_DAYS) -> None:
    """Перегенерировать `01_mood/График настроения.md` — заметку с блоком Obsidian
    Charts (рендерит community-плагин «Obsidian Charts»; Python для рисования не нужен).
    Дневное среднее PAD за последние `days` дней. Вызывается после `append_point`.
    """
    try:
        labels, series = aggregate_daily(_read_recent_points(days))
        if not labels:
            return
        block = [
            "```chart",
            "type: line",
            f"labels: {json.dumps(labels, ensure_ascii=False)}",
            "series:",
            f"  - title: валентность\n    data: {json.dumps(series['valence'])}",
            f"  - title: энергия\n    data: {json.dumps(series['arousal'])}",
            f"  - title: доминирование\n    data: {json.dumps(series['dominance'])}",
            "```",
        ]
        body = (
            "# График настроения\n\n"
            "Дневное среднее PAD (валентность/энергия/доминирование, ∈[-1..1]) по "
            f"последним {days} дням. Источник — `01_mood/timeseries/`.\n"
            "Требует community-плагин **Obsidian Charts** (иначе блок ниже не отрисуется).\n\n"
            + "\n".join(block) + "\n"
        )
        d = userctx.user_root() / "01_mood"
        d.mkdir(parents=True, exist_ok=True)
        atomic_write_text(d / _CHART_NOTE, body)
    except Exception:
        log.exception("rebuild_chart failed (non-fatal)")
