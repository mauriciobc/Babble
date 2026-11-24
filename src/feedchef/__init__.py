# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef: recipe-driven client-side timelines (mashdon port).

Catch Up is a period digest of home. Feed Chef is a continuous timeline
built from a declarative recipe (pantry → diet → kitchen). See
``Docs/feed-chef-adr.md``.
"""

from .recipe import FeedRecipe, default_recipe, migrate_recipe
from .storage import FeedRecipeStorage

__all__ = [
    'FeedRecipe', 'FeedRecipeStorage', 'default_recipe', 'migrate_recipe',
]
