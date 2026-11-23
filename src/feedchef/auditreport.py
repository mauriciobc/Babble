# SPDX-License-Identifier: GPL-3.0-or-later
"""An audit record in plain language (no widgets, no GTK).

One place words what the audit knows, so the dialogs of the app and the
``show`` command of ``bench/feedchef_audit.py`` agree. Every function returns
rows of text a caller lays out: it takes a ``Words`` (the app's gettext and
ngettext; ``ENGLISH`` by default) and never imports a translation module.
The text the functions read is the record only: nothing here names a post's
text, author or handle, and ``report_text`` carries counts, verdicts, a hash
and dates only.
"""

from __future__ import annotations

import locale
from typing import Any, Callable, NamedTuple


class Row(NamedTuple):
    title: str
    subtitle: str = ''
    value: str = ''


class Section(NamedTuple):
    title: str
    rows: list


class Words(NamedTuple):
    gettext: Callable[[str], str]
    ngettext: Callable[[str, str, int], str]


def _plural(one: str, other: str, count: int) -> str:
    return one if count == 1 else other


ENGLISH = Words(lambda text: text, _plural)

# A tone is neutral inside this band around zero (the VADER convention).
NEUTRAL_BAND = 0.05


def number(value: Any, digits: int = 1) -> str:
    """*value* with the reader's decimal separator."""
    try:
        return locale.format_string('%.*f', (digits, float(value)),
                                    grouping=True)
    except (TypeError, ValueError):
        return ''


def whole(value: Any) -> str:
    try:
        return locale.format_string('%d', int(value), grouping=True)
    except (TypeError, ValueError):
        return ''


def _factor(value: Any, words: Words) -> str:
    _ = words.gettext
    try:
        shown = locale.format_string('%.3g', float(value))
    except (TypeError, ValueError):
        return ''
    # Translators: a multiplier, for example "× 1.3"; %s is the number.
    return _('× %s') % shown


def _scope_name(scope: Any, words: Words) -> str:
    _ = words.gettext
    return {'home': _('Your home timeline'),
            'local': _('The local timeline'),
            'federated': _('The federated timeline'),
            'list': _('One of your lists')}.get(scope, _('Your timeline'))


def _language_name(code: Any, words: Words) -> str:
    _ = words.gettext
    lang = str(code or '').lower().replace('_', '-')
    if lang == 'en' or lang.startswith('en-'):
        return _('English')
    if lang == 'pt' or lang.startswith('pt-'):
        return _('Portuguese')
    return str(code or '')


def reason_text(reason: str, words: Words = ENGLISH) -> str:
    """Why a post was removed, as a short phrase."""
    _ = words.gettext
    if reason.startswith('guard:'):
        return _('Removed by a safety check')
    known = {
        'pantry_excluded_tag': _('Had a hashtag you excluded'),
        'pantry_missing_included_tag':
            _('Had none of the hashtags you included'),
        'diet:keyword_blocklist': _('Contained a blocked word'),
        'diet:must_have_media': _('Had no media'),
        'diet:must_have_alt_text': _('Had media without a description'),
        'diet:no_sensitive_media': _('Marked as sensitive'),
        'diet:min_content_length': _('Too short'),
        'diet:positive_only': _('Read as negative'),
        'reach_not_followed': _('Not from someone you follow'),
        'viewer_hide': _('Hidden by your mutes and filters'),
        'heat_cut': _('Below the popularity cutoff'),
        'author_cap': _('Over the limit per person'),
    }
    return known.get(reason) or _('Removed for another reason')


# -- one post -------------------------------------------------------------

def _origin_text(label: str, scope: Any, words: Words) -> str:
    _ = words.gettext
    kind, _sep, name = str(label).partition(':')
    if kind == 'hashtag':
        # Translators: %s is a hashtag name, without the #.
        return _('The hashtag #%s') % name
    if kind == 'focus':
        # Translators: %s is a word or hashtag from a More Like This search.
        return _('A More Like This search for “%s”') % name
    return _scope_name(scope, words)


def _origin_rows(entry: dict, snapshot: dict, words: Words) -> list[Row]:
    _ = words.gettext
    scope = (snapshot.get('pantry') or {}).get('scope')
    rows = [Row(_('Found Through'),
                _origin_text(entry.get('origin') or 'scope', scope, words))]
    also = [_origin_text(label, scope, words)
            for label in entry.get('also_in') or []]
    if also:
        rows.append(Row(_('Also Found Through'), ', '.join(also)))
    if (entry.get('facts') or {}).get('boost'):
        rows.append(Row(_('Boost'), _('Someone you can see boosted it')))
    return rows


def _rule_rows(words: Words) -> dict[str, tuple[str, str]]:
    _ = words.gettext
    diet, pantry = _('Diet'), _('Pantry')
    return {
        'keyword_blocklist': (_('No Blocked Words'), diet),
        'must_have_media': (_('Has Media'), diet),
        'must_have_alt_text': (_('Media Has Descriptions'), diet),
        'no_sensitive_media': (_('Not Marked as Sensitive'), diet),
        'min_content_length': (_('Long Enough'), diet),
        'positive_only': (_('Not Negative'), diet),
        'hashtags_exclude': (_('No Excluded Hashtags'), pantry),
        'hashtags_include': (_('Has an Included Hashtag'), pantry),
    }


def _tone(score: Any, words: Words) -> str:
    _ = words.gettext
    try:
        value = float(score)
    except (TypeError, ValueError):
        return ''
    if value >= NEUTRAL_BAND:
        return _('Positive')
    if value <= -NEUTRAL_BAND:
        return _('Negative')
    return _('Neutral')


def _check_rows(entry: dict, words: Words) -> list[Row]:
    _ = words.gettext
    rules = entry.get('rules') or {}
    titles = _rule_rows(words)
    follow = (entry.get('follow') or {}).get('verdict')
    rows = []
    for promise, verdict in rules.items():
        if verdict != 'pass':
            continue
        if promise == 'network_reach':
            rows.append(Row(
                _('Your Own Post') if follow == 'self'
                else _('From Someone You Follow'), _('Pantry')))
        elif promise in titles:
            title, section = titles[promise]
            rows.append(Row(title, section))
    if entry.get('viewer') == 'shown':
        rows.append(Row(_('Allowed by Your Filters'),
                        _('Mutes and keyword filters')))
    sent = entry.get('sent')
    if sent:
        facts = entry.get('facts') or {}
        rows.append(Row(_('Language'), '',
                        _language_name(facts.get('lang'), words)))
        rows.append(Row(_('Tone'), '', _tone(sent.get('score'), words)))
    if not rows:
        rows.append(Row(_('No Rules Applied'),
                        _('Your recipe sets no limits that apply to '
                          'this post')))
    return rows


def _reactions(facts: dict, words: Words) -> str:
    ngettext = words.ngettext
    fav, boost, reply = (int(facts.get(key) or 0)
                         for key in ('fav', 'reblog', 'reply'))
    parts = []
    if fav:
        parts.append(ngettext('%s favorite', '%s favorites', fav)
                     % whole(fav))
    if boost:
        parts.append(ngettext('%s boost', '%s boosts', boost)
                     % whole(boost))
    if reply:
        parts.append(ngettext('%s reply', '%s replies', reply)
                     % whole(reply))
    return ', '.join(parts) or words.gettext('No reactions yet')


def _modifier_types(snapshot: dict) -> set[str]:
    return {str(mod.get('type')) for mod in
            (snapshot.get('kitchen') or {}).get('modifiers') or []
            if isinstance(mod, dict)}


def _rank_rows(entry: dict, record: dict, snapshot: dict,
               words: Words) -> list[Row]:
    _ = words.gettext
    score = entry.get('score')
    if not score:
        return [Row(_('Not Scored'), _('This post was not ranked'))]
    types = _modifier_types(snapshot)
    rows = [Row(_('Reactions'), _reactions(entry.get('facts') or {}, words),
                number(score.get('base')))]
    for key, kind, title, hint in (
            ('fresh', 'freshness', _('Freshness'),
             _('Newer posts count for more')),
            ('sent', 'sentiment_boost', _('Positive Tone'),
             _('Positive posts count for more')),
            ('trend', 'trending', _('Trending'),
             _('Posts on rising topics count for more'))):
        factor = score.get(key)
        if kind in types or (factor is not None and factor != 1.0):
            rows.append(Row(title, hint, _factor(factor, words)))
    rows.append(Row(_('Score'), '', number(score.get('final'))))
    rank = entry.get('rank')
    if isinstance(rank, int) and rank >= 0:
        total = (record.get('summary') or {}).get('selected') or 0
        page = int(record.get('page_index') or 0)
        # Translators: the position of a post among those shown, for
        # example "3 of 34"; the first number is its place.
        rows.append(Row(
            _('Position'),
            _('On page %s of this timeline') % whole(page + 1)
            if page else '',
            _('%(place)s of %(total)s') % {'place': whole(rank + 1),
                                           'total': whole(total)}))
    return rows


def _removal_rows(entry: dict, words: Words) -> list[Row]:
    _ = words.gettext
    removal = entry.get('removal') or {}
    reasons = [str(removal.get('reason') or ''),
               *[str(item) for item in removal.get('also') or []]]
    seen = []
    for reason in reasons:
        text = reason_text(reason, words)
        if text not in seen:
            seen.append(text)
    return [Row(_('Removed'), ', '.join(seen))]


def explain_post(entry: dict, record: dict,
                 words: Words = ENGLISH) -> list[Section]:
    """The sections that explain one post: where it came from, the checks
    it passed and how it was ranked (or why it was removed)."""
    _ = words.gettext
    snapshot = (record.get('recipe') or {}).get('snapshot') or {}
    sections = [
        Section(_('Where It Came From'), _origin_rows(entry, snapshot,
                                                      words)),
        Section(_('Checks It Passed'), _check_rows(entry, words))]
    if entry.get('removal'):
        sections.append(Section(_('Why It Was Removed'),
                                _removal_rows(entry, words)))
    else:
        sections.append(Section(
            _('How It Was Ranked'),
            _rank_rows(entry, record, snapshot, words)))
    return sections


# -- one cook -------------------------------------------------------------

class Report(NamedTuple):
    pages: int
    gathered: int
    selected: int
    guard: int
    removed: dict
    settings: str
    sections: list
    text: str


def settings_line(snapshot: dict, words: Words = ENGLISH) -> str:
    """The recipe's settings in one line: counts and kinds, no names."""
    _ = words.gettext
    ngettext = words.ngettext
    pantry = snapshot.get('pantry') or {}
    diet = snapshot.get('diet') or {}
    kitchen = snapshot.get('kitchen') or {}
    parts = [_scope_name(pantry.get('scope'), words)]
    reach = pantry.get('network_reach') or {}
    if reach.get('enabled') and reach.get('max_degrees') is not None \
            and not reach.get('include_unknown'):
        parts.append(_('Only people you follow'))
    included = len(pantry.get('hashtags_include') or [])
    excluded = len(pantry.get('hashtags_exclude') or [])
    if included:
        parts.append(ngettext('%s hashtag included',
                              '%s hashtags included', included)
                     % whole(included))
    if excluded:
        parts.append(ngettext('%s hashtag excluded',
                              '%s hashtags excluded', excluded)
                     % whole(excluded))
    rules = sum(1 for on in (diet.get('hard_filters') or {}).values() if on)
    rules += 1 if diet.get('keyword_blocklist') else 0
    if rules:
        parts.append(ngettext('%s diet rule', '%s diet rules', rules)
                     % whole(rules))
    parts.append(_('Newest first')
                 if kitchen.get('base_sort') == 'chronological'
                 else _('Most engaging first'))
    names = {'freshness': _('Freshness'), 'sentiment_boost': _('Positive Tone'),
             'trending': _('Trending'), 'heat': _('Popularity cutoff'),
             'diversity': _('Limit per person')}
    mods = [names[kind] for kind in ('freshness', 'sentiment_boost',
                                     'trending', 'heat', 'diversity')
            if kind in _modifier_types(snapshot)]
    if mods:
        parts.append(', '.join(mods))
    # Translators: separates the parts of a one-line summary.
    return _(' · ').join(parts)


def _text(report_facts: dict) -> str:
    """The text of Copy Report, in English whatever the language: it is
    pasted into a bug report. Counts, verdicts, a hash and dates only."""
    lines = ['Feed Chef report',
             'App: %(app)s, engine revision %(rev)s' % report_facts,
             'Recipe hash: %(hash)s' % report_facts,
             'Settings: %(settings)s' % report_facts,
             'Cook: %(pages)d pages, %(first)s to %(last)s'
             % report_facts,
             'Posts gathered: %(gathered)d' % report_facts,
             'Posts shown: %(selected)d' % report_facts,
             'Removed by a safety check: %(guard)d' % report_facts,
             'Removed, by reason:']
    removed = report_facts['removed']
    for reason in sorted(removed, key=lambda key: (-removed[key], key)):
        lines.append('  %s: %d' % (reason, removed[reason]))
    return '\n'.join(lines) + '\n'


def cook_report(records: list[dict], words: Words = ENGLISH) -> Report:
    """What one cook did, summed over its pages: posts gathered, each
    reason posts were removed for, and posts shown. *records* are the
    audit records of the cook's pages."""
    _ = words.gettext
    usable = [record for record in records
              if (record.get('outcome') or {}).get('status') == 'ok']
    gathered = selected = duplicates = 0
    removed: dict[str, int] = {}
    for record in usable:
        summary = record.get('summary') or {}
        gathered += int(summary.get('raw_fetched') or 0)
        selected += int(summary.get('selected') or 0)
        duplicates += int(summary.get('duplicates') or 0)
        for reason, count in (summary.get('by_reason') or {}).items():
            removed[reason] = removed.get(reason, 0) + int(count)
    guard = sum(count for reason, count in removed.items()
                if reason.startswith('guard:'))
    last = usable[-1] if usable else {}
    snapshot = (last.get('recipe') or {}).get('snapshot') or {}
    settings = settings_line(snapshot, words)

    merged: dict[str, int] = {}
    for reason, count in removed.items():
        text = reason_text(reason, words)
        merged[text] = merged.get(text, 0) + count
    left_out = [Row(text, '', whole(count)) for text, count in
                sorted(merged.items(), key=lambda item: (-item[1], item[0]))]
    if duplicates:
        left_out.append(Row(_('Seen more than once'), '', whole(duplicates)))
    if not left_out:
        left_out.append(Row(_('Nothing was left out')))
    sections = [
        Section(_('Last Cook'), [
            Row(_('Posts Gathered'), '', whole(gathered)),
            Row(_('Posts Shown'), '', whole(selected))]),
        Section(_('Left Out'), left_out)]
    dates = [str((record.get('clock') or {}).get('now_utc') or '')
             for record in usable]
    facts = {
        'app': (last.get('app') or {}).get('version') or 'unknown',
        'rev': last.get('engine_rev', 'unknown'),
        'hash': str((last.get('recipe') or {}).get('hash') or '')[:12],
        'settings': settings_line(snapshot, ENGLISH),
        'pages': len(usable), 'first': dates[0] if dates else '',
        'last': dates[-1] if dates else '', 'gathered': gathered,
        'selected': selected, 'guard': guard, 'removed': removed}
    return Report(len(usable), gathered, selected, guard, removed,
                  settings, sections, _text(facts))


def render(sections: list[Section], out: Callable[[str], None],
           indent: str = '  ') -> None:
    """Lay *sections* out as lines (the command line's version of the
    dialogs): a heading, then a row each as ``title  subtitle  value``."""
    for section in sections:
        out('%s:' % section.title)
        for row in section.rows:
            parts = [row.title]
            if row.subtitle:
                parts.append('(%s)' % row.subtitle)
            if row.value:
                parts.append(row.value)
            out(indent + '  '.join(parts))
