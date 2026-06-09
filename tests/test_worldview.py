from __future__ import annotations

from random import Random

from bot import userctx, worldview
from bot.worldview import Evidence, WorldviewAtom
from bot.worldview_taxonomy import WORLDVIEW_AREAS, choose_random_target, get_category


def test_random_target_is_always_valid():
    rng = Random(42)
    for _ in range(100):
        target = choose_random_target(rng)
        area = next(a for a in WORLDVIEW_AREAS if a.key == target["area"])
        category = get_category(area.key, target["category"])
        assert category is not None
        assert target["theme"] in category.themes
        assert target["theme_key"] == f"{target['area']}/{target['category']}/{target['theme']}"


def test_worldview_atom_roundtrip_and_cross_links(as_user):
    first = WorldviewAtom(
        slug="chestnost",
        name="Честность",
        area="values_norms",
        category="norms",
        theme="честность",
        type="principle",
        summary="Считает честность правилом близости.",
        evidence=[Evidence(when="2026-06-09 10:00", text="я не вру своим", raw_ref="[[00_raw/qna/2026-06-09#^Q1]]")],
    )
    second = WorldviewAtom(
        slug="postupok",
        name="Поступок",
        area="practice",
        category="actions",
        theme="выбор в конфликте",
        type="action",
        summary="Проверяет слова действием.",
        related=["chestnost"],
    )

    worldview.save_atom(first)
    worldview.save_atom(second)
    worldview.add_relation("chestnost", "postupok", "contradicts")

    loaded = worldview.load_atom("postupok")
    assert loaded is not None
    assert loaded.related == ["chestnost"]
    text = (userctx.user_root() / "04_Практический уровень" / "atoms" / "postupok.md").read_text(encoding="utf-8")
    assert "[[03_Ценностно-нормативная подсистема/atoms/chestnost|Честность]]" in text

    check = worldview.check_links()
    assert check["broken"] == []
    assert check["asymmetric"] == []
