"""Сборка русского эмо-лексикона `bot/data/nrc_emolex_ru.tsv` из NRC-EmoLex.

Источник — NRC Emotion Lexicon (EmoLex, Mohammad & Turney), русская ветка
авто-перевода: `OneFilePerLanguage/Russian-NRC-EmoLex.txt` из официального архива
https://saifmohammad.com/WebDocs/Lexicons/NRC-Emotion-Lexicon.zip
Лицензия: research / non-commercial (как и NRC-VAD).

Формат входа: `English Word \t anger anticipation disgust fear joy negative
positive sadness surprise trust \t Russian Word` (значения 0/1, первая строка — шапка).

Формат выхода (читает `bot/emolex.py`): `russian_word` + 8 эмоций Плутчика +
positive/negative (0/1), порядок:
`word \t anger \t anticipation \t disgust \t fear \t joy \t sadness \t surprise \t trust \t positive \t negative`
Одно слово на строку (фразы с пробелами отброшены), дубликаты слиты по OR (max).

Запуск (Docker):
    docker run --rm -v "%cd%":/app -v "<dir>":/src -w /app python:3.12-slim \
        python scripts/build_emolex.py /src/Russian-NRC-EmoLex.txt
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "bot" / "data" / "nrc_emolex_ru.tsv"
# Индексы колонок входа (0-based) → выходной порядок.
# вход: 0 eng,1 anger,2 anticipation,3 disgust,4 fear,5 joy,6 negative,7 positive,
#       8 sadness,9 surprise,10 trust,11 russian
_OUT_ORDER = [
    ("anger", 1), ("anticipation", 2), ("disgust", 3), ("fear", 4), ("joy", 5),
    ("sadness", 8), ("surprise", 9), ("trust", 10), ("positive", 7), ("negative", 6),
]


def build(src: Path) -> None:
    agg: dict[str, list[int]] = defaultdict(lambda: [0] * len(_OUT_ORDER))
    with src.open(encoding="utf-8") as f:
        next(f, None)  # шапка
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 12:
                continue
            word = parts[11].strip().lower()
            if not word or " " in word:
                continue
            try:
                flags = [int(parts[idx]) for _, idx in _OUT_ORDER]
            except ValueError:
                continue
            cur = agg[word]
            for i, v in enumerate(flags):
                if v:
                    cur[i] = 1  # OR-слияние дубликатов

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for word in sorted(agg):
        flags = agg[word]
        if not any(flags):
            continue  # нейтральные слова не несут сигнала — не храним
        rows.append(word + "\t" + "\t".join(str(v) for v in flags))
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} entries → {OUT}")
    print("columns:", ", ".join(name for name, _ in _OUT_ORDER))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: build_emolex.py <Russian-NRC-EmoLex.txt>")
    build(Path(sys.argv[1]))
