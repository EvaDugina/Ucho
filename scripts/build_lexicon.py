"""Сборка русского VAD-лексикона `bot/data/nrc_vad_ru.tsv` из NRC-VAD.

Источник — NRC-VAD-Lexicon (Saif Mohammad, ACL 2018), русская ветка авто-перевода
(Google Translate, Aug 2022): файл `OneFilePerLanguage/Russian-NRC-VAD-Lexicon.txt`
из официального архива https://saifmohammad.com/WebDocs/Lexicons/NRC-VAD-Lexicon.zip

Лицензия NRC-VAD: бесплатно для research / non-commercial. Для PoC B приемлемо;
на коммерческих стадиях — пересмотреть (см. .docs/technical.md).

Формат входа: `English Word \t Valence \t Arousal \t Dominance \t Russian Word`
(значения ∈ [0..1], первая строка — шапка).

Формат выхода (то, что читает `bot/lexicon.py`): `russian_word \t v \t a \t d`,
одно слово на строку (фразы с пробелами отброшены — рантайм матчит по токенам),
дубликаты русского слова усреднены.

Запуск (Docker, как требует проект):
    docker run --rm -v "%cd%":/app -v "<dir-с-txt>":/src -w /app python:3.12-slim \
        python scripts/build_lexicon.py /src/Russian-NRC-VAD-Lexicon.txt
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "bot" / "data" / "nrc_vad_ru.tsv"


def build(src: Path) -> None:
    agg: dict[str, list[tuple[float, float, float]]] = defaultdict(list)
    with src.open(encoding="utf-8") as f:
        next(f, None)  # шапка
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 5:
                continue
            try:
                v, a, d = float(parts[1]), float(parts[2]), float(parts[3])
            except ValueError:
                continue
            word = parts[4].strip().lower()
            if not word or " " in word:  # пустое / фраза → пропуск (токен-матч)
                continue
            agg[word].append((v, a, d))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for word in sorted(agg):
        vals = agg[word]
        n = len(vals)
        v = sum(x[0] for x in vals) / n
        a = sum(x[1] for x in vals) / n
        d = sum(x[2] for x in vals) / n
        rows.append(f"{word}\t{v:.3f}\t{a:.3f}\t{d:.3f}")
    OUT.write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(f"wrote {len(rows)} entries → {OUT}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: build_lexicon.py <Russian-NRC-VAD-Lexicon.txt>")
    build(Path(sys.argv[1]))
