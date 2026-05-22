"""Юнит-тесты графа концептов: roundtrip, slug-barrier, дедуп, alias-resolve.

Используют фикстуру ``as_user`` (реальная запись в изолированный tmp-вольт).
"""
from __future__ import annotations

from bot import graph
from bot.graph import Concept


def _save(slug, domain, summary, **kw):
    c = Concept(
        slug=slug,
        name=kw.get("name", slug),
        type="value",
        domain=domain,
        summary=summary,
        status="draft",
        aliases=kw.get("aliases", []),
    )
    return graph.save_concept(c)


def test_save_and_load_roundtrip(as_user):
    p = _save("chestnost", "ethics", "Честность это основа доверия между людьми во всех делах")
    assert p is not None
    c = graph.load_concept("chestnost", "ethics")
    assert c is not None
    assert c.slug == "chestnost"
    assert c.domain == "ethics"


def test_save_concept_rejects_bad_slug(as_user):
    # slug санитизируется в "" → отказ записи (path-traversal / мусор barrier).
    c = Concept(slug="!!!", name="bad", type="value", domain="ethics", summary="x")
    assert graph.save_concept(c) is None


def test_find_similar_concept_hit(as_user):
    summary = "Свобода это способность выбирать свой путь без принуждения извне"
    _save("svoboda", "politics", summary)
    near = "Свобода это способность выбирать свой путь без принуждения со стороны"
    found = graph.find_similar_concept(near, "politics", threshold=0.6)
    assert found is not None
    assert found.slug == "svoboda"


def test_find_similar_concept_miss(as_user):
    _save("trud", "work", "Труд облагораживает человека и придаёт смысл будням")
    found = graph.find_similar_concept(
        "Совершенно другая мысль про закат солнца над морем летом", "work", threshold=0.7
    )
    assert found is None


def test_resolve_slug_direct_and_alias(as_user):
    _save(
        "druzhba",
        "relationships",
        "Дружба это форма близости основанная на взаимном доверии и заботе",
        aliases=["товарищество"],
    )
    assert graph.resolve_slug("druzhba", "relationships") == "druzhba"
    assert graph.resolve_slug("товарищество", "relationships") == "druzhba"
    assert graph.resolve_slug("несуществующее-понятие", "relationships") is None
