"""Одноразовая миграция концептов из 4-доменной схемы в 10-доменную.

Старые домены: ethics, aesthetics, politics, everyday.
Новые: ethics, aesthetics, politics, everyday, relationships, identity,
mortality, nationality, knowledge, work.

Скрипт — двухфазный:

* ``python scripts/migrate_domains.py`` (без аргументов) — **dry-run**.
  Для каждого концепта вызывает LLM-классификатор, складывает план в
  ``<vault>/.psycho/migration-proposal.md``. Ничего не двигает.
* ``python scripts/migrate_domains.py --apply`` — читает proposal, двигает
  файлы, обновляет ``domain:`` во frontmatter, патчит wikilinks в других
  концептах. Всё внутри ``git_wrap("migrate")``.

Запускать ТОЛЬКО в Docker (правило CLAUDE.md):
  docker compose run --rm bot python scripts/migrate_domains.py [--apply]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

# Скрипт лежит в scripts/, бот в bot/. Чтобы импорты `from bot import ...`
# работали, добавляем корень проекта в sys.path.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot import graph, vault  # noqa: E402
from bot.atomic import atomic_write_text  # noqa: E402
from bot.config import DOMAINS, PSYCHO_META_DIR  # noqa: E402

LEGACY_DOMAINS = {"ethics", "aesthetics", "politics", "everyday"}
PROPOSAL_PATH = PSYCHO_META_DIR / "migration-proposal.md"


_CLASSIFIER_PROMPT = """Ты классификатор концептов в графе внутреннего мира.

Дан концепт. Выбери ровно ОДИН из 10 доменов, который ему лучше всего подходит:
- ethics — мораль, правильно/неправильно
- aesthetics — вкус, красота, искусство
- politics — власть, общество, справедливость
- everyday — быт, привычки, рутина
- relationships — близкие отношения, любовь, дружба
- identity — кто я, самоопределение, ценности «Я»
- mortality — конечность, страх смерти, смысл
- nationality — страна, культура, этничность
- knowledge — познание, истина, знания
- work — труд, дело, карьера, мастерство

Верни JSON: {"domain": "<один из 10>", "confidence": <0..1>, "reason": "<одна фраза>"}.
"""


def _classify_with_llm(concept: graph.Concept) -> dict:
    """Синхронный wrapper над LLM-классификатором.

    AITunnel вызывается через async openai-compatible client, поэтому крутим
    в asyncio.run.
    """
    from openai import AsyncOpenAI

    from bot.config import (
        AITUNNEL_API_KEY,
        AITUNNEL_BASE_URL,
        LLM_FALLBACK_PROCESS,
        LLM_MODEL_PROCESS,
        LLM_TIMEOUT,
    )

    client = AsyncOpenAI(
        api_key=AITUNNEL_API_KEY,
        base_url=AITUNNEL_BASE_URL,
        timeout=LLM_TIMEOUT,
        max_retries=1,
    )
    models = tuple(dict.fromkeys((LLM_MODEL_PROCESS, *LLM_FALLBACK_PROCESS)))

    user_msg = (
        f"Концепт:\n"
        f"- slug: {concept.slug}\n"
        f"- name: {concept.name}\n"
        f"- type: {concept.type}\n"
        f"- старый_домен: {concept.domain}\n"
        f"- summary: {concept.summary}\n"
    )

    async def go() -> dict:
        last_error = ""
        for model in models:
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": _CLASSIFIER_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.0,
                )
                raw = resp.choices[0].message.content or "{}"
                return json.loads(raw)
            except Exception as exc:
                last_error = f"{model}: {exc!r}"
        return {"domain": concept.domain, "confidence": 0.0, "reason": last_error or "LLM failed"}

    try:
        return asyncio.run(go())
    except Exception as exc:
        return {"domain": concept.domain, "confidence": 0.0, "reason": f"LLM error: {exc!r}"}


def _propose() -> list[dict]:
    """Пройтись по концептам legacy-доменов и предложить новый домен."""
    proposals: list[dict] = []
    for d in sorted(LEGACY_DOMAINS):
        domain_dir = graph.CONCEPTS_DIR / d
        if not domain_dir.is_dir():
            continue
        for p in sorted(domain_dir.glob("*.md")):
            if p.name.startswith("_"):
                continue
            c = graph._parse_file(p)
            if c is None:
                continue
            cls = _classify_with_llm(c)
            new_d = cls.get("domain")
            if new_d not in DOMAINS:
                new_d = c.domain  # фолбэк
            proposals.append({
                "slug": c.slug,
                "old_domain": c.domain,
                "new_domain": new_d,
                "confidence": cls.get("confidence", 0.0),
                "reason": cls.get("reason", ""),
                "name": c.name,
            })
            print(f"  {c.slug}: {c.domain} → {new_d} ({cls.get('confidence', 0):.2f}) — {cls.get('reason', '')[:80]}")
    return proposals


def _write_proposal(proposals: list[dict]) -> None:
    lines = [
        "# Migration proposal: 4 domains → 10",
        "",
        f"Concepts: {len(proposals)}.",
        f"Will move: {sum(1 for p in proposals if p['old_domain'] != p['new_domain'])}.",
        "",
        "| slug | old | new | conf | reason |",
        "|---|---|---|---|---|",
    ]
    for p in proposals:
        lines.append(
            f"| {p['slug']} | {p['old_domain']} | {p['new_domain']} | "
            f"{p['confidence']:.2f} | {p['reason'][:80]} |"
        )
    lines.append("")
    lines.append("После проверки запусти `python scripts/migrate_domains.py --apply` чтобы применить.")
    atomic_write_text(PROPOSAL_PATH, "\n".join(lines) + "\n")
    # Сохраним структурированно для apply-фазы — рядом, JSON.
    atomic_write_text(
        PROPOSAL_PATH.with_suffix(".json"),
        json.dumps(proposals, ensure_ascii=False, indent=2) + "\n",
    )


def _read_proposal_json() -> Optional[list[dict]]:
    p = PROPOSAL_PATH.with_suffix(".json")
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        return None


# Регекс для замены domain в frontmatter.
_FM_DOMAIN_RE = re.compile(r"^(domain:\s*)(\S+)\s*$", re.MULTILINE)


def _apply_one(prop: dict) -> bool:
    """Переместить один концепт + поправить frontmatter. Возвращает True если что-то изменилось."""
    slug = prop["slug"]
    old_d = prop["old_domain"]
    new_d = prop["new_domain"]
    if old_d == new_d:
        return False
    src = graph.CONCEPTS_DIR / old_d / f"{slug}.md"
    dst = graph.CONCEPTS_DIR / new_d / f"{slug}.md"
    if not src.exists():
        vault.append_log("warn", "migrate_missing", f"src {src} gone")
        return False
    if dst.exists():
        vault.append_log("warn", "migrate_collision", f"target {dst} already exists, skipped")
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    body = src.read_text("utf-8")
    new_body = _FM_DOMAIN_RE.sub(rf"\1{new_d}", body, count=1)
    atomic_write_text(dst, new_body)
    src.unlink()
    vault.append_log("info", "migrate_moved", f"{slug}: {old_d} → {new_d}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply proposal (write changes)")
    args = parser.parse_args()

    vault.ensure_layout()

    if not args.apply:
        print("=== DRY RUN: classifying concepts via LLM ===")
        proposals = _propose()
        _write_proposal(proposals)
        moved = sum(1 for p in proposals if p["old_domain"] != p["new_domain"])
        print(f"\nProposal written: {PROPOSAL_PATH}")
        print(f"Concepts: {len(proposals)}, will move: {moved}")
        print(f"Review the proposal, then run with --apply")
        return 0

    proposals = _read_proposal_json()
    if proposals is None:
        print("No proposal found. Run without --apply first to generate it.")
        return 1
    print(f"=== APPLY: {len(proposals)} proposals ===")
    moved = 0
    touched_domains: set[str] = set()
    with vault.git_wrap("migrate_domains"):
        for prop in proposals:
            if _apply_one(prop):
                moved += 1
                touched_domains.add(prop["old_domain"])
                touched_domains.add(prop["new_domain"])
        # MOC rebuild для каждого затронутого домена
        from bot import moc
        for d in touched_domains:
            if d in DOMAINS:
                try:
                    moc.rebuild_domain_moc(d)
                except Exception as exc:
                    print(f"  MOC rebuild failed for {d}: {exc!r}")
    print(f"\nMoved: {moved}/{len(proposals)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
