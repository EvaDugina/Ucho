"""Каноническая таксономия мировоззрения для вопросов и графа 01-04.

Старые домены больше не являются runtime-контрактом вопросов. Вопрос всегда
порождается из программно выбранной тройки: область -> категория -> тема. LLM
получает эту тройку как жёсткую рамку и только формулирует живой вопрос.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class WorldviewCategory:
    key: str
    title: str
    description: str
    themes: tuple[str, ...]


@dataclass(frozen=True)
class WorldviewArea:
    key: str
    folder: str
    title: str
    description: str
    categories: tuple[WorldviewCategory, ...]

    @property
    def number(self) -> str:
        return self.folder.split("_", 1)[0]


WORLDVIEW_AREAS: tuple[WorldviewArea, ...] = (
    WorldviewArea(
        key="sensation",
        folder="01_Мироощущение",
        title="Мироощущение",
        description="Эмоционально-чувственный способ переживания мира до рационального объяснения.",
        categories=(
            WorldviewCategory(
                key="emotions",
                title="Эмоции и чувства",
                description="Отдельные эмоции и чувства.",
                themes=("радость", "страх", "тревога", "надежда", "стыд", "вина", "гнев", "нежность", "отвращение", "удивление"),
            ),
            WorldviewCategory(
                key="mood_background",
                title="Устойчивый эмоциональный фон",
                description="Длительный эмоциональный фон, через который человек воспринимает происходящее.",
                themes=("апатия", "воодушевление", "внутреннее напряжение", "спокойствие", "тоска", "раздражение", "собранность"),
            ),
            WorldviewCategory(
                key="world_tone",
                title="Тон мира",
                description="Каким мир ощущается человеку на чувственном уровне.",
                themes=("мир добрый", "мир враждебный", "мир равнодушный", "мир хрупкий", "мир опасный", "мир щедрый"),
            ),
            WorldviewCategory(
                key="beauty_ugliness",
                title="Прекрасное и уродливое",
                description="Чувственное восприятие красоты, гармонии, пошлости и пустоты.",
                themes=("красота", "уродство", "гармония", "пошлость", "чистота", "грязь", "величие", "пустота"),
            ),
            WorldviewCategory(
                key="body_and_energy",
                title="Тело и энергия",
                description="Телесное переживание жизни, силы, усталости, зажатости и перегруза.",
                themes=("усталость", "сила", "зажатость", "свобода тела", "бессилие", "азарт", "сонливость", "перегруз"),
            ),
            WorldviewCategory(
                key="existential_feeling",
                title="Экзистенциальное чувство",
                description="Базовые чувства существования: одиночество, принадлежность, конечность, будущее.",
                themes=("одиночество", "принадлежность", "бездомность", "укоренённость", "конечность", "ожидание будущего"),
            ),
        ),
    ),
    WorldviewArea(
        key="understanding",
        folder="02_Миропонимание",
        title="Миропонимание",
        description="Рационально-теоретическая картина мира: что человек считает истинным и как объясняет происходящее.",
        categories=(
            WorldviewCategory(
                key="knowledge",
                title="Знания и источники знания",
                description="То, откуда человек берёт знания и чему доверяет как источнику понимания.",
                themes=("наука", "религия", "житейский опыт", "образование", "авторитеты", "традиция", "интуиция", "личный эксперимент"),
            ),
            WorldviewCategory(
                key="beliefs",
                title="Убеждения",
                description="Твёрдо принятые идеи о мире, человеке, свободе, добре и зле.",
                themes=("природа человека", "общество", "справедливость мира", "прогресс", "судьба", "свобода воли", "зло", "добро"),
            ),
            WorldviewCategory(
                key="principles",
                title="Принципы мышления и решения",
                description="Внутренние правила, по которым человек думает, проверяет и принимает решения.",
                themes=("не лгать себе", "проверять факты", "держать слово", "сомневаться", "искать причины", "не верить толпе"),
            ),
            WorldviewCategory(
                key="causality",
                title="Причинность",
                description="Как человек объясняет причины событий и распределяет ответственность.",
                themes=("случайность", "закономерность", "личная ответственность", "система", "характер", "обстоятельства"),
            ),
            WorldviewCategory(
                key="self_world_model",
                title="Модель себя в мире",
                description="Рациональное понимание себя, своих возможностей, ограничений и роли.",
                themes=("кто я", "на что способен", "в чём ограничен", "что мной движет", "где моя роль"),
            ),
            WorldviewCategory(
                key="uncertainty",
                title="Неизвестность",
                description="Отношение к неясности, доказательствам, вере и риску ошибки.",
                themes=("терпимость к неясности", "потребность в доказательствах", "вера без доказательств", "страх ошибки"),
            ),
        ),
    ),
    WorldviewArea(
        key="values_norms",
        folder="03_Ценностно-нормативная подсистема",
        title="Ценностно-нормативная подсистема",
        description="Что человек считает важным, должным, хорошим, запретным и достойным стремления.",
        categories=(
            WorldviewCategory(
                key="values",
                title="Ценности",
                description="Значимые блага и то, что человек считает важным, хорошим и правильным.",
                themes=("свобода", "семья", "справедливость", "деньги", "власть", "любовь", "достоинство", "безопасность", "развитие"),
            ),
            WorldviewCategory(
                key="ideals",
                title="Идеалы",
                description="Образы совершенства, к которым человек считает достойным стремиться.",
                themes=("идеальный человек", "идеальная жизнь", "идеальная любовь", "идеальная работа", "идеальное общество"),
            ),
            WorldviewCategory(
                key="norms",
                title="Нормы",
                description="Правила поведения, должного отношения и допустимых границ.",
                themes=("вежливость", "долг", "верность", "честность", "уважение", "забота", "взаимность", "границы"),
            ),
            WorldviewCategory(
                key="taboos",
                title="Табу",
                description="То, что человек считает недопустимым и внутренне запрещённым.",
                themes=("предательство", "унижение слабого", "трусость", "ложь", "насилие", "зависимость", "продажность"),
            ),
            WorldviewCategory(
                key="hierarchy",
                title="Иерархия ценностей",
                description="Порядок важности ценностей и конфликтующие пары выбора.",
                themes=("свобода vs безопасность", "любовь vs долг", "правда vs милосердие", "деньги vs смысл"),
            ),
            WorldviewCategory(
                key="judgement",
                title="Оценка себя и других",
                description="Как человек судит, прощает, требует, восхищается или презирает.",
                themes=("вина", "заслуженность", "прощение", "наказание", "презрение", "восхищение", "требовательность"),
            ),
        ),
    ),
    WorldviewArea(
        key="practice",
        folder="04_Практический уровень",
        title="Практический уровень",
        description="Как мировоззрение становится действием, стилем жизни, волей и поступком.",
        categories=(
            WorldviewCategory(
                key="readiness",
                title="Готовность действовать",
                description="Готовность начать, рискнуть, защитить, признаться, уйти или попросить помощи.",
                themes=("начать", "рискнуть", "защитить", "признаться", "уйти", "попросить помощи", "отказаться"),
            ),
            WorldviewCategory(
                key="will",
                title="Воля",
                description="Способность реализовывать взгляды через дисциплину, выдержку и самоконтроль.",
                themes=("дисциплина", "выдержка", "слабость воли", "упорство", "самоконтроль", "срыв", "привычка"),
            ),
            WorldviewCategory(
                key="lifestyle",
                title="Стиль жизни",
                description="Устойчивый образ жизни: быт, работа, отдых, отношения, режим и порядок.",
                themes=("быт", "работа", "отдых", "отношения", "одиночество", "режим", "хаос", "порядок", "потребление"),
            ),
            WorldviewCategory(
                key="actions",
                title="Поступки",
                description="Конкретные выборы и действия в конфликте, помощи, заботе, уступке или борьбе.",
                themes=("выбор в конфликте", "помощь", "отказ", "забота", "месть", "уступка", "борьба", "бегство"),
            ),
            WorldviewCategory(
                key="strategies",
                title="Стратегии совладания",
                description="Способы справляться с миром: избегание, контроль, переговоры и рационализация.",
                themes=("избегание", "контроль", "переговоры", "давление", "терпение", "ирония", "рационализация"),
            ),
            WorldviewCategory(
                key="consequences",
                title="Последствия выбора",
                description="Связь взглядов и результата: цена, повторяющийся паттерн, компромисс и самообман.",
                themes=("цена выбора", "повторяющийся паттерн", "компромисс", "самообман", "победа", "поражение"),
            ),
        ),
    ),
)

AREA_BY_KEY: Mapping[str, WorldviewArea] = {a.key: a for a in WORLDVIEW_AREAS}
AREA_BY_FOLDER: Mapping[str, WorldviewArea] = {a.folder: a for a in WORLDVIEW_AREAS}
AREA_FOLDERS: tuple[str, ...] = tuple(a.folder for a in WORLDVIEW_AREAS)
GENERAL_FOLDER = "05_Общее"
WORLDVIEW_TYPES = ("feeling", "belief", "principle", "value", "ideal", "norm", "taboo", "strategy", "action", "pattern", "claim")
LEGACY_DOMAIN_TARGETS = {
    "ethics": ("values_norms", "norms", "честность"),
    "aesthetics": ("sensation", "beauty_ugliness", "красота"),
    "politics": ("understanding", "beliefs", "общество"),
    "everyday": ("practice", "lifestyle", "быт"),
    "relationships": ("practice", "actions", "отношения"),
    "identity": ("understanding", "self_world_model", "кто я"),
    "mortality": ("sensation", "existential_feeling", "конечность"),
    "nationality": ("understanding", "beliefs", "общество"),
    "knowledge": ("understanding", "knowledge", "житейский опыт"),
    "work": ("practice", "lifestyle", "работа"),
}


def area_keys() -> tuple[str, ...]:
    return tuple(AREA_BY_KEY)


def get_area(area: str | None) -> Optional[WorldviewArea]:
    if not area:
        return None
    return AREA_BY_KEY.get(area) or AREA_BY_FOLDER.get(area)


def get_category(area: str | None, category: str | None) -> Optional[WorldviewCategory]:
    a = get_area(area)
    if a is None or not category:
        return None
    for c in a.categories:
        if c.key == category:
            return c
    return None


def theme_key(area: str, category: str, theme: str) -> str:
    return f"{area}/{category}/{theme}"


def is_valid_target(area: str | None, category: str | None, theme: str | None, *, allow_custom: bool = True) -> bool:
    a = get_area(area)
    c = get_category(area, category)
    if a is None or c is None or not theme:
        return False
    return allow_custom or theme in c.themes


def coerce_target(area: str | None, category: str | None, theme: str | None) -> dict:
    """Нормализовать target. Невалидное значение заменяется первым пунктом канона."""
    a = get_area(area) or WORLDVIEW_AREAS[0]
    c = get_category(a.key, category) or a.categories[0]
    t = str(theme or "").strip() or c.themes[0]
    return target_dict(a, c, t)


def legacy_domain_target(domain: str | None) -> dict | None:
    if domain not in LEGACY_DOMAIN_TARGETS:
        return None
    return coerce_target(*LEGACY_DOMAIN_TARGETS[domain])


def target_dict(area: WorldviewArea, category: WorldviewCategory, theme: str) -> dict:
    return {
        "area": area.key,
        "area_folder": area.folder,
        "area_title": area.title,
        "area_description": area.description,
        "category": category.key,
        "category_title": category.title,
        "category_description": category.description,
        "theme": str(theme).strip(),
        "theme_key": theme_key(area.key, category.key, str(theme).strip()),
    }


def choose_random_target(rng: random.Random | None = None, area: str | None = None) -> dict:
    """Равномерный выбор: область -> категория -> тема."""
    r = rng or random
    a = get_area(area) or r.choice(WORLDVIEW_AREAS)
    c = r.choice(a.categories)
    t = r.choice(c.themes)
    return target_dict(a, c, t)


def all_targets() -> Iterable[dict]:
    for a in WORLDVIEW_AREAS:
        for c in a.categories:
            for t in c.themes:
                yield target_dict(a, c, t)


def match_hint(hint: str | None) -> dict | None:
    """Грубый deterministic matcher для targeted-режима.

    Если hint совпал с темой/категорией/областью — возвращаем канонический target.
    Если не совпал, вызывающий может создать одноразовый custom_theme.
    """
    h = (hint or "").strip().lower()
    if not h:
        return None
    best: dict | None = None
    best_score = 0
    for target in all_targets():
        hay = " ".join([
            target["theme"],
            target["category"],
            target["category_title"],
            target["category_description"],
            target["area_title"],
            target["area_description"],
        ]).lower()
        score = 0
        if h == target["theme"].lower():
            score = 100
        elif h in hay:
            score = 50 + len(h)
        else:
            for token in h.split():
                if len(token) >= 4 and token in hay:
                    score += 5
        if score > best_score:
            best = target
            best_score = score
    return best if best_score >= 5 else None


def custom_target_from_hint(hint: str, fallback: dict | None = None) -> dict:
    base = fallback or choose_random_target()
    a = get_area(base.get("area")) or WORLDVIEW_AREAS[0]
    c = get_category(a.key, base.get("category")) or a.categories[0]
    return target_dict(a, c, str(hint or "custom_theme").strip()[:120] or "custom_theme")


def describe_taxonomy() -> str:
    """Короткое описание канона для prompts/docs/selfcheck."""
    lines: list[str] = []
    for area in WORLDVIEW_AREAS:
        lines.append(f"- {area.folder}: {area.description}")
        for category in area.categories:
            themes = ", ".join(category.themes)
            lines.append(f"  - {category.key}: {category.description} Темы: {themes}.")
    return "\n".join(lines)
