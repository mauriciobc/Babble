# SPDX-License-Identifier: GPL-3.0-or-later
"""A recipe in one plain sentence (no widgets, no GTK).

"Posts from Home about #cats, without sensitive posts, most engaging
first": what the builder shows at its top while the reader edits, and what
the recipe list shows under each recipe's name. Every piece is a whole
translatable phrase, and the pieces go into whole-sentence templates (one
per source), so a translator can reorder them; nothing is built by gluing
single words. It takes a ``Words`` (the app's gettext and ngettext;
``ENGLISH`` by default), like the audit report, and never imports a
translation module.
"""

from __future__ import annotations

from .auditreport import ENGLISH, Words
from .recipe import FeedRecipe

# Hashtags the sentence names before "and N more".
TAGS_SHOWN = 3
# Phrases of the middle of the sentence before "and N more".
EXTRAS_SHOWN = 4


def _tags(tags: list[str], words: Words) -> str:
    shown = ' '.join('#%s' % tag for tag in tags[:TAGS_SHOWN])
    extra = len(tags) - TAGS_SHOWN
    if extra <= 0:
        return shown
    # Translators: %(tags)s lists the first hashtags, such as "#a #b #c",
    # and %(count)d is how many more there are.
    return words.ngettext('%(tags)s and %(count)d more',
                          '%(tags)s and %(count)d more',
                          extra) % {'tags': shown, 'count': extra}


def _source(recipe: FeedRecipe, words: Words) -> str:
    _ = words.gettext
    tags = _tags(recipe.pantry.hashtags_include, words)
    scope = recipe.pantry.scope
    if tags:
        known = {
            # Translators: the start of a recipe's sentence; %s lists
            # hashtags, such as "#cats #dogs".
            'home': _('Posts from Home about %s'),
            # Translators: as above, for the local timeline.
            'local': _('Posts from the local timeline about %s'),
            # Translators: as above, for the federated timeline.
            'federated': _('Posts from the federated timeline about %s'),
            # Translators: as above, for one of the reader's lists.
            'list': _('Posts from a list about %s'),
        }
        return known.get(scope, _('Posts about %s')) % tags
    return {
        # Translators: the start of a recipe's sentence, with no hashtags.
        'home': _('Posts from Home'),
        'local': _('Posts from the local timeline'),
        'federated': _('Posts from the federated timeline'),
        'list': _('Posts from a list'),
    }.get(scope, _('Posts'))


def _extras(recipe: FeedRecipe, words: Words) -> list[str]:
    """The filters and tweaks the recipe has, each as a phrase that fits
    after a comma, in a fixed order (what is left out first, then what is
    asked for, then how the posts are ranked)."""
    _ = words.gettext
    ngettext = words.ngettext
    pantry, hard = recipe.pantry, recipe.diet.hard_filters
    found: list[str] = []
    if pantry.hashtags_exclude:
        # Translators: %s lists hashtags, such as "#a #b"; the posts that
        # carry any of them are left out.
        found.append(_('without %s') % _tags(pantry.hashtags_exclude, words))
    blocked = len(recipe.diet.keyword_blocklist)
    if blocked:
        found.append(ngettext('without %d blocked word',
                              'without %d blocked words', blocked) % blocked)
    if hard.must_have_media:
        found.append(_('only posts with media'))
    if hard.must_have_alt_text:
        found.append(_('with alt text on media'))
    if hard.no_sensitive_media:
        found.append(_('without sensitive posts'))
    if hard.positive_only:
        found.append(_('only positive posts'))
    if hard.min_content_length:
        found.append(ngettext('at least %d character',
                              'at least %d characters',
                              hard.min_content_length)
                     % hard.min_content_length)
    if pantry.network_reach.enabled:
        found.append(_('only people you follow'))
    for mod in recipe.kitchen.modifiers:
        phrase = _modifier(mod, words)
        if phrase:
            found.append(phrase)
    return found


def _modifier(mod, words: Words) -> str:
    _ = words.gettext
    ngettext = words.ngettext
    if mod.type == 'freshness':
        return _('favoring fresh posts')
    if mod.type == 'trending':
        return _('boosting trending topics')
    if mod.type == 'sentiment_boost':
        return _('favoring positive posts')
    if mod.type == 'heat' and (mod.percentile_threshold or 0) > 0:
        return _('hiding the least engaging')
    if mod.type == 'diversity':
        count = max(1, int(mod.max_per_cluster or 1))
        if 'author' not in (mod.diversity_dimensions or ['author']):
            return ngettext('up to %d post per server',
                            'up to %d posts per server', count) % count
        return ngettext('up to %d post per person',
                        'up to %d posts per person', count) % count
    if mod.type == 'similarity_cluster':
        count = max(1, int(mod.max_per_cluster or 1))
        return ngettext('up to %d post per group of similar ones',
                        'up to %d posts per group of similar ones',
                        count) % count
    return ''


def _order(recipe: FeedRecipe, words: Words) -> str:
    _ = words.gettext
    kitchen = recipe.kitchen
    terms = ['#%s' % tag for tag in kitchen.active_focus_hashtags or []]
    terms += list(kitchen.active_focus_keywords or [])
    if terms:
        # Translators: the end of a recipe's sentence; %s lists the
        # hashtags and words the timeline is focused on.
        return _('most like %s first') % ', '.join(terms[:TAGS_SHOWN])
    if kitchen.base_sort == 'chronological':
        return _('newest first')
    return _('most engaging first')


def _join(phrases: list[str], words: Words) -> str:
    """Phrases as a list that reads aloud: a whole template for each length
    (up to four), so a language can put its own "and" and commas where it
    needs them."""
    _ = words.gettext
    letters = dict(zip('abcd', phrases, strict=False))
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        # Translators: two phrases of a recipe's sentence, such as "without
        # sensitive posts and only posts with media".
        return _('%(a)s and %(b)s') % letters
    if len(phrases) == 3:
        # Translators: three phrases, such as "a, b and c".
        return _('%(a)s, %(b)s and %(c)s') % letters
    # Translators: four phrases, such as "a, b, c and d".
    return _('%(a)s, %(b)s, %(c)s and %(d)s') % letters


def recipe_sentence(recipe: FeedRecipe, words: Words = ENGLISH) -> str:
    """The recipe as one sentence that reads from left to right."""
    _ = words.gettext
    extras = _extras(recipe, words)
    if len(extras) > EXTRAS_SHOWN:
        left = len(extras) - EXTRAS_SHOWN + 1
        extras = extras[:EXTRAS_SHOWN - 1] + [words.ngettext(
            '%d more setting', '%d more settings', left) % left]
    parts = {'source': _source(recipe, words),
             'order': _order(recipe, words)}
    if not extras:
        # Translators: a recipe's sentence with nothing left out, such as
        # "Posts from Home, most engaging first".
        return _('%(source)s, %(order)s') % parts
    parts['extras'] = _join(extras, words)
    # Translators: a recipe's sentence, such as "Posts from Home about
    # #cats, without sensitive posts, most engaging first".
    return _('%(source)s, %(extras)s, %(order)s') % parts


def chef_line(recipe: FeedRecipe, words: Words = ENGLISH) -> str:
    """"Chef: @user@instance" for a recipe that has an author, else ''."""
    author = (recipe.meta.author or '').strip()
    if not author:
        return ''
    # Translators: who wrote a recipe, such as "Chef: @user@example.social".
    return words.gettext('Chef: %s') % author


def list_subtitle(recipe: FeedRecipe, words: Words = ENGLISH) -> str:
    """What the recipe list shows under a recipe's name: the sentence, and
    the chef when the recipe has one."""
    text = recipe_sentence(recipe, words)
    chef = chef_line(recipe, words)
    if not chef:
        return text
    # Translators: a recipe's sentence and then its chef, such as "Posts
    # from Home, most engaging first · Chef: @user@example.social".
    return words.gettext('%(sentence)s · %(chef)s') % {
        'sentence': text, 'chef': chef}
