"""Миграция legacy `02_concepts` в новый worldview-граф 01-04.

Dry-run по умолчанию:

    python scripts/migrate_worldview_schema.py --vault /vault/users/123

Apply:

    python scripts/migrate_worldview_schema.py --vault /vault/users/123 --apply

`00_raw` не меняется. Концепты перекладываются детерминированным mapping старых
доменов в area/category/theme; смысловую переклассификацию после этого делает
сильная модель.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.worldview_taxonomy import legacy_domain_target  # noqa: E402


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Migrate legacy concepts to worldview folders")
    p.add_argument("--vault", required=True, help="Path to one user vault root, e.g. /vault/users/123")
    p.add_argument("--apply", action="store_true", help="Actually move files; default is dry-run")
    return p.parse_args()


def load_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    try:
        fm = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        fm = {}
    return fm if isinstance(fm, dict) else {}, m.group(2)


def dump_frontmatter(fm: dict, body: str) -> str:
    head = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False, default_flow_style=False).strip()
    return f"---\n{head}\n---\n{body.lstrip()}"


def migrate_file(src: Path, user_root: Path, apply: bool) -> dict:
    domain = src.parent.name
    target = legacy_domain_target(domain)
    if target is None:
        target = legacy_domain_target("everyday")
    dst_dir = user_root / target["area_folder"] / "atoms"
    dst = dst_dir / src.name
    text = src.read_text(encoding="utf-8")
    fm, body = load_frontmatter(text)
    fm.pop("domain", None)
    fm["area"] = target["area"]
    fm["category"] = target["category"]
    fm["theme"] = target["theme"]
    fm.setdefault("type", "claim")
    fm.setdefault("status", "draft")
    for key in ("supports", "contradicts", "derived_from", "related"):
        fm[key] = _rewrite_relation_list(fm.get(key) or [])
    fm.setdefault("influences", [])
    fm.setdefault("manifests_as", [])
    new_text = dump_frontmatter(fm, body)
    if apply:
        dst_dir.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            dst = dst_dir / f"{src.stem}-legacy.md"
        dst.write_text(new_text, encoding="utf-8")
        src.unlink()
    return {
        "from": str(src.relative_to(user_root)),
        "to": str(dst.relative_to(user_root)),
        "domain": domain,
        "area": target["area"],
        "category": target["category"],
        "theme": target["theme"],
    }


def _rewrite_relation_list(values: list) -> list[str]:
    out: list[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if not text:
            continue
        if text.startswith("[[") and text.endswith("]]"):
            text = text[2:-2]
        if "|" in text:
            target, label = text.split("|", 1)
        else:
            target, label = text, ""
        slug = target.rsplit("/", 1)[-1]
        out.append(f"[[{slug}|{label or slug}]]")
    return out


def migrate_personality(user_root: Path, apply: bool) -> list[dict]:
    moves = [
        (user_root / "03_personality" / "about.md", user_root / "05_Общее" / "about.md"),
        (user_root / "03_personality" / "deltas.jsonl", user_root / "05_Общее" / "deltas.jsonl"),
        (user_root / "03_personality" / "user_prompt.md", user_root / "05_Общее" / "user_prompt.md"),
        (user_root / "03_personality" / "profile.md", user_root / "05_Общее" / "profile.md"),
        (user_root / "03_personality" / "softskills.md", user_root / "05_Общее" / "softskills.md"),
        (user_root / "03_personality" / "mask_frequencies.json", user_root / "05_Общее" / "mask_frequencies.json"),
        (user_root / "03_personality" / "mask_frequencies_draft.json", user_root / "05_Общее" / "mask_frequencies_draft.json"),
        (user_root / "03_personality" / "face_actions.json", user_root / "05_Общее" / "face_actions.json"),
        (user_root / "03_personality" / "liked_replies.json", user_root / "05_Общее" / "liked_replies.json"),
        (user_root / "03_personality" / "liked_replies_log.jsonl", user_root / "05_Общее" / "liked_replies_log.jsonl"),
        (user_root / "03_personality" / "mood.md", user_root / "01_Мироощущение" / "mood" / "mood.md"),
        (user_root / "01_mood" / "feedback.jsonl", user_root / "01_Мироощущение" / "mood" / "feedback.jsonl"),
        (user_root / "01_mood" / "_mood_map.json", user_root / "01_Мироощущение" / "mood" / "_mood_map.json"),
    ]
    report: list[dict] = []
    for src, dst in moves:
        if not src.exists():
            continue
        report.append({"from": str(src.relative_to(user_root)), "to": str(dst.relative_to(user_root))})
        if apply:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists():
                continue
            shutil.move(str(src), str(dst))
    for sub in ("events", "analysis", "timeseries"):
        src_dir = user_root / "01_mood" / sub
        dst_dir = user_root / "01_Мироощущение" / "mood" / sub
        if not src_dir.exists():
            continue
        for src in src_dir.glob("*"):
            dst = dst_dir / src.name
            report.append({"from": str(src.relative_to(user_root)), "to": str(dst.relative_to(user_root))})
            if apply:
                dst_dir.mkdir(parents=True, exist_ok=True)
                if not dst.exists():
                    shutil.move(str(src), str(dst))
    return report


def main() -> int:
    args = parse_args()
    user_root = Path(args.vault).resolve()
    concepts = user_root / "02_concepts"
    if not user_root.exists():
        raise SystemExit(f"vault not found: {user_root}")
    report: dict[str, object] = {"dry_run": not args.apply, "concepts": [], "moves": []}
    if concepts.exists():
        for src in sorted(concepts.glob("*/*.md")):
            if src.name.startswith("_") or src.stem == src.parent.name.upper():
                continue
            report["concepts"].append(migrate_file(src, user_root, args.apply))
    report["moves"] = migrate_personality(user_root, args.apply)
    report_path = user_root / "05_Общее" / "worldview_migration_report.json"
    if args.apply:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
