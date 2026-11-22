# SPDX-License-Identifier: GPL-3.0-or-later
"""The recipe icon table: mashdon icon enum names to icon names.

``recipe.meta.icon`` keeps the enum name a shared recipe carries (the
default is ``PIZZA``). Only icons the app can draw are listed, each once;
a name that is not in the table, or a legacy value, shows the chef hat.
The reader's label for each entry lives with the builder, which
translates it. Standard library only."""

DEFAULT_ICON = 'babble-feed-chef-symbolic'

# (enum name, icon name)
ICONS = (
    ('PIZZA', 'emoji-food-symbolic'),
    ('NATURE', 'emoji-nature-symbolic'),
    ('ACTIVITIES', 'emoji-activities-symbolic'),
    ('OBJECTS', 'emoji-objects-symbolic'),
    ('PEOPLE', 'emoji-people-symbolic'),
    ('TRAVEL', 'emoji-travel-symbolic'),
    ('FLAGS', 'emoji-flags-symbolic'),
    ('SYMBOLS', 'emoji-symbols-symbolic'),
    ('STAR', 'starred-symbolic'),
    ('NEWS', 'babble-news-symbolic'),
    ('HASHTAG', 'babble-hashtag-symbolic'),
    ('BOOKMARK', 'user-bookmarks-symbolic'),
    ('HOME', 'user-home-symbolic'),
    ('GAMES', 'applications-games-symbolic'),
    ('SCIENCE', 'applications-science-symbolic'),
    ('MUSIC', 'folder-music-symbolic'),
    ('VIDEO', 'camera-video-symbolic'),
    ('PHOTO', 'camera-photo-symbolic'),
    ('WEATHER', 'weather-clear-symbolic'),
    ('TERMINAL', 'utilities-terminal-symbolic'),
    ('HAPPY', 'face-smile-symbolic'),
    ('COOL', 'face-cool-symbolic'),
    ('CHEF', DEFAULT_ICON),
)

_BY_ENUM = dict(ICONS)


def icon_name(enum):
    """The icon for a recipe's ``meta.icon``; the chef hat when unknown."""
    return _BY_ENUM.get(str(enum or '').strip().upper(), DEFAULT_ICON)
