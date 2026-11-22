# SPDX-License-Identifier: GPL-3.0-or-later
"""FeedRecipe model — JSON field names match mashdon ``@SerializedName``.

Behaviour SoT: mashdon ``FeedRecipe.java`` (master @ 23277cd). Fields the
Java engine ignores are still stored so round-trips stay lossless.
"""

from __future__ import annotations

import copy
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any


def _norm_tag(tag: str) -> str:
    text = (tag or '').strip()
    if text.startswith('#'):
        text = text[1:]
    return text.lower()


def normalize_hashtags(tags: list[str] | None) -> list[str]:
    return [_norm_tag(tag) for tag in (tags or ()) if _norm_tag(tag)]


@dataclass
class RecipeMeta:
    name: str = 'Untitled Recipe'
    icon: str = 'PIZZA'
    author: str = ''
    version: int = 1


@dataclass
class NetworkReachConfig:
    enabled: bool = False
    max_degrees: int | None = None  # None = filter off (Java), not unlimited
    include_self: bool = True
    include_unknown: bool = False


@dataclass
class PantryConfig:
    scope: str = 'home'  # home | local | federated | list
    list_id: str | None = None
    hashtags_include: list[str] = field(default_factory=list)
    hashtags_exclude: list[str] = field(default_factory=list)
    fetch_strategy: str = 'mix'  # persisted; engine ignores
    network_reach: NetworkReachConfig = field(default_factory=NetworkReachConfig)


@dataclass
class HardFilters:
    must_have_media: bool = False
    must_have_alt_text: bool = False
    no_sensitive_media: bool = False
    min_content_length: int = 0
    positive_only: bool = False


@dataclass
class DietConfig:
    hard_filters: HardFilters = field(default_factory=HardFilters)
    keyword_blocklist: list[str] = field(default_factory=list)
    # Legacy root field; cleared by migrate_recipe.
    min_content_length: int | None = None


@dataclass
class Modifier:
    type: str = ''
    decay_minutes: int | None = None
    bonus_score: int | None = None
    percentile_threshold: int | None = None
    similarity_threshold: float | None = None
    max_per_cluster: int | None = None
    cluster_strategy: str | None = None
    diversity_dimensions: list[str] | None = None
    sentiment_boost: float | None = None
    time_window_hours: int | None = None  # dead on Java trending path
    growth_threshold: float | None = None


@dataclass
class KitchenConfig:
    base_sort: str = 'score'  # chronological | score
    modifiers: list[Modifier] = field(default_factory=list)
    active_focus_keywords: list[str] | None = None
    active_focus_weights: list[float] | None = None
    focus_decay: float = 1.0  # persisted; engine ignores in v1
    enable_fuzzy_matching: bool = True  # v1 treats as off for synonyms
    active_focus_hashtags: list[str] | None = None


@dataclass
class FeedRecipe:
    id: str = ''
    meta: RecipeMeta = field(default_factory=RecipeMeta)
    pantry: PantryConfig = field(default_factory=PantryConfig)
    diet: DietConfig = field(default_factory=DietConfig)
    kitchen: KitchenConfig = field(default_factory=KitchenConfig)

    def inert_modifiers(self) -> list[str]:
        """Modifier types that need NLP/gap work and are inert in v1 core."""
        inert = []
        for mod in self.kitchen.modifiers:
            if mod.type in INERT_WITHOUT_GAP:
                inert.append(mod.type)
        if self.kitchen.active_focus_keywords:
            inert.append('active_focus_keywords')
        return sorted(set(inert))


def default_recipe(name: str = 'New Recipe') -> FeedRecipe:
    """Mashdon builder default: home scope + diversity max 3 author."""
    return FeedRecipe(
        id=str(uuid.uuid4()),
        meta=RecipeMeta(name=name or 'New Recipe'),
        pantry=PantryConfig(scope='home'),
        diet=DietConfig(),
        kitchen=KitchenConfig(
            base_sort='score',
            modifiers=[Modifier(
                type='diversity',
                max_per_cluster=3,
                diversity_dimensions=['author'],
            )],
        ),
    )


def migrate_recipe(data: dict[str, Any]) -> dict[str, Any]:
    """In-memory migrate matching ``FeedRecipe.migrate()``."""
    recipe = copy.deepcopy(data) if data else {}
    diet = recipe.get('diet')
    if not isinstance(diet, dict):
        return recipe
    hard = diet.get('hard_filters')
    if hard is None:
        diet['hard_filters'] = hard = {}
    legacy = diet.get('min_content_length')
    if legacy is not None and int(hard.get('min_content_length') or 0) == 0:
        hard['min_content_length'] = int(legacy)
        diet['min_content_length'] = None
    recipe['diet'] = diet
    return recipe


def _reach_from(raw: dict | None) -> NetworkReachConfig:
    raw = raw or {}
    return NetworkReachConfig(
        enabled=bool(raw.get('enabled', False)),
        max_degrees=raw.get('max_degrees'),
        include_self=bool(raw.get('include_self', True)),
        include_unknown=bool(raw.get('include_unknown', False)),
    )


def _modifier_from(raw: dict) -> Modifier:
    return Modifier(
        type=str(raw.get('type') or ''),
        decay_minutes=raw.get('decay_minutes'),
        bonus_score=raw.get('bonus_score'),
        percentile_threshold=raw.get('percentile_threshold'),
        similarity_threshold=raw.get('similarity_threshold'),
        max_per_cluster=raw.get('max_per_cluster'),
        cluster_strategy=raw.get('cluster_strategy'),
        diversity_dimensions=raw.get('diversity_dimensions'),
        sentiment_boost=raw.get('sentiment_boost'),
        time_window_hours=raw.get('time_window_hours'),
        growth_threshold=raw.get('growth_threshold'),
    )


def recipe_from_dict(data: dict[str, Any] | None) -> FeedRecipe:
    data = migrate_recipe(data or {})
    meta_raw = data.get('meta') or {}
    pantry_raw = data.get('pantry') or {}
    diet_raw = data.get('diet') or {}
    hard_raw = diet_raw.get('hard_filters') or {}
    kitchen_raw = data.get('kitchen') or {}
    recipe = FeedRecipe(
        id=str(data.get('id') or ''),
        meta=RecipeMeta(
            name=str(meta_raw.get('name') or 'Untitled Recipe'),
            icon=str(meta_raw.get('icon') or 'PIZZA'),
            author=str(meta_raw.get('author') or ''),
            version=int(meta_raw.get('version') or 1),
        ),
        pantry=PantryConfig(
            scope=str(pantry_raw.get('scope') or 'home'),
            list_id=pantry_raw.get('list_id'),
            hashtags_include=normalize_hashtags(
                pantry_raw.get('hashtags_include')),
            hashtags_exclude=normalize_hashtags(
                pantry_raw.get('hashtags_exclude')),
            fetch_strategy=str(pantry_raw.get('fetch_strategy') or 'mix'),
            network_reach=_reach_from(pantry_raw.get('network_reach')),
        ),
        diet=DietConfig(
            hard_filters=HardFilters(
                must_have_media=bool(hard_raw.get('must_have_media')),
                must_have_alt_text=bool(hard_raw.get('must_have_alt_text')),
                no_sensitive_media=bool(hard_raw.get('no_sensitive_media')),
                min_content_length=int(hard_raw.get('min_content_length') or 0),
                positive_only=bool(hard_raw.get('positive_only')),
            ),
            keyword_blocklist=[
                str(word).strip().lower()
                for word in (diet_raw.get('keyword_blocklist') or ())
                if str(word).strip()
            ],
            min_content_length=diet_raw.get('min_content_length'),
        ),
        kitchen=KitchenConfig(
            base_sort=str(kitchen_raw.get('base_sort') or 'score'),
            modifiers=[
                _modifier_from(item)
                for item in (kitchen_raw.get('modifiers') or ())
                if isinstance(item, dict)
            ],
            active_focus_keywords=kitchen_raw.get('active_focus_keywords'),
            active_focus_weights=kitchen_raw.get('active_focus_weights'),
            focus_decay=float(kitchen_raw.get('focus_decay')
                              if kitchen_raw.get('focus_decay') is not None
                              else 1.0),
            enable_fuzzy_matching=bool(
                kitchen_raw.get('enable_fuzzy_matching', True)),
            active_focus_hashtags=(
                normalize_hashtags(kitchen_raw.get('active_focus_hashtags'))
                if kitchen_raw.get('active_focus_hashtags') is not None
                else None),
        ),
    )
    return recipe


def recipe_to_dict(recipe: FeedRecipe) -> dict[str, Any]:
    payload = asdict(recipe)
    # Drop cleared legacy null unless present for round-trip clarity.
    if payload['diet'].get('min_content_length') is None:
        payload['diet'].pop('min_content_length', None)
    return payload


def validate_for_save(recipe: FeedRecipe) -> FeedRecipe:
    """Storage validateRecipe: require id; empty name → Untitled Recipe."""
    if not recipe.id:
        raise ValueError('recipe id is required')
    if not (recipe.meta.name or '').strip():
        recipe.meta.name = 'Untitled Recipe'
    return recipe


def validate_for_import(data: dict[str, Any]) -> FeedRecipe:
    """Import activity: meta required; empty id → new UUID."""
    if data is None or data.get('meta') is None:
        raise ValueError('recipe meta is required')
    recipe = recipe_from_dict(data)
    if not recipe.id:
        recipe.id = str(uuid.uuid4())
    if not (recipe.meta.name or '').strip():
        recipe.meta.name = 'Untitled Recipe'
    return recipe


# Modifiers active without further NLP gap work in v1.
ACTIVE_MODIFIER_TYPES = frozenset({
    'freshness', 'heat', 'diversity', 'trending', 'sentiment_boost',
})
# Still inert: cluster/TF-IDF and connection (no synonyms / embeddings).
INERT_WITHOUT_GAP = frozenset({
    'similarity_cluster', 'connection',
})
