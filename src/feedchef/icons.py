# SPDX-License-Identifier: GPL-3.0-or-later
"""The recipe icon table: mashdon icon enum names to icon names.

``recipe.meta.icon`` keeps the name a shared recipe carries, which is
the name of mashdon's ``TimelineDefinition.Icon`` Java enum (the default
is ``PIZZA``). Only the enum names whose meaning can be shown with an
icon the app can draw are listed, each with an icon of its own; any other
name, the Java names left out included, shows the chef hat. The reader's
label for each entry lives with the builder, which translates it.
Standard library only."""

DEFAULT_ICON = 'babble-feed-chef-symbolic'

# (enum name, icon name)
ICONS = (
    ('PIZZA', 'emoji-food-symbolic'),
    ('HEART', 'emote-love-symbolic'),
    ('STAR', 'starred-symbolic'),
    ('PEOPLE', 'emoji-people-symbolic'),
    ('NEWS', 'babble-news-symbolic'),
    ('IMAGE', 'image-x-generic-symbolic'),
    ('MUSIC', 'audio-x-generic-symbolic'),
    ('GAMES', 'applications-games-symbolic'),
    ('TAG', 'babble-hashtag-symbolic'),
    ('PIN', 'view-pin-symbolic'),
    ('WEATHER', 'weather-few-clouds-symbolic'),
    ('LEAVES', 'emoji-nature-symbolic'),
    ('SPORT', 'emoji-activities-symbolic'),
    ('TRAIN', 'emoji-travel-symbolic'),
    ('LAUGH', 'face-laugh-symbolic'),
    ('SHIELD', 'security-high-symbolic'),
    ('IMPORTANT', 'dialog-warning-symbolic'),
    ('CODE', 'utilities-terminal-symbolic'),
    ('MICROPHONE', 'audio-input-microphone-symbolic'),
    ('BOOK', 'accessories-dictionary-symbolic'),
)

_BY_ENUM = dict(ICONS)


def icon_name(enum):
    """The icon for a recipe's ``meta.icon``; the chef hat when unknown."""
    return _BY_ENUM.get(str(enum or '').strip().upper(), DEFAULT_ICON)
