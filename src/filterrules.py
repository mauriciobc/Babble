# SPDX-License-Identifier: GPL-3.0-or-later

"""What one of the server's filter rules means for one status.

Two places have to answer that and they must not disagree: the timeline
row that draws the "Filtered:" bar (``timeline.status_filter``) and Catch
Up, which drops a ``hide`` post outright (``catchup.digest.filter_info``).
Before this module they were two copies, and they had drifted -- the
timeline read neither ``Filter#context`` nor ``Filter#expires_at``, so a
rule scoped to another view still collapsed a post in the home timeline
while Catch Up honoured it. The answer now lives here once.

Deliberately free of ``gi``. ``catchup.digest`` imports it and has to stay
importable without a display -- ``test-catchup-digest.py`` asserts ``gi``
never enters ``sys.modules`` -- and ``timeline.py`` imports it from the GTK
side. Nothing here builds a widget; this is what the API says a rule
means, not what to do about it.

The reader lives here rather than in ``statustext`` (the app-wide home)
because of that import rule, not by preference. It is one reader either
way; if the two ever diverge, every view of a filter would disagree with
every other, which is the bug ``filter rules`` exists to catch.
"""

import datetime

# ``Filter#context`` (docs.joinmastodon.org/entities/Filter) is where a
# rule applies: home, notifications, public, thread, account. The page
# says which one it is showing and only rules naming it (or naming none,
# which is what Mastodon sends for a rule made before contexts existed)
# are considered.
HOME = 'home'
NOTIFICATIONS = 'notifications'
PUBLIC = 'public'
THREAD = 'thread'
ACCOUNT = 'account'

# An action nobody documents is treated as the mildest one that still
# tells the reader something happened, so a server that invents a new
# action cannot quietly hide a post.
DEFAULT_ACTION = 'warn'

# Actions weaker than this: they show the post with something applied.
# 'hide' is the only action a view may act on destructively.
HIDE = 'hide'
BLUR = 'blur'


def parse_time(value):
    """Aware ``datetime`` from an ISO-8601 string, or None.

    A naive result is taken as UTC -- what every Mastodon timestamp is.
    Also accepts a datetime, because Catch Up resolves its window bounds
    from both shapes. The one reader in the app; see the module docstring
    for why it lives here and not in statustext.
    """
    if isinstance(value, datetime.datetime):
        dt = value
    elif isinstance(value, str) and value:
        text = value.strip()
        if text.endswith(('Z', 'z')):
            text = text[:-1] + '+00:00'
        try:
            dt = datetime.datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


def _live_rules(status, context, now):
    """The rules that apply to *status* in *context* right now.

    Both the wrapper and the post it boosts are read: a boost is filtered
    by a rule on either half. A rule naming other contexts is not ours.

    Expiry is judged only against a *now* the caller supplied. Left out,
    no rule is treated as expired -- which is what keeps a Catch Up
    snapshot reading the same way every time it is opened, since that
    module never reads the clock. A live view that wants expiry honoured
    passes the current time.
    """
    rules = []
    for source in (status, status.get('reblog') or {}):
        for result in source.get('filtered') or []:
            rule = result.get('filter') or {}
            contexts = rule.get('context')
            if contexts and context not in contexts:
                continue
            rules.append(rule)
    if not rules or now is None:
        return rules
    now = parse_time(now)
    return [rule for rule in rules
            if _not_expired(rule.get('expires_at'), now)]


def _not_expired(expires_at, now):
    """Whether a rule carrying *expires_at* still applies at *now*."""
    if not expires_at:
        return True
    expires = parse_time(expires_at)
    return expires is None or expires > now


def resolve(status, context=HOME, now=None):
    """``{'action': ..., 'titles': [...]}`` for the strongest rule that
    applies to *status*, or None when none does.

    ``hide`` beats every other action. Otherwise the result is ``blur``
    only when every rule that hit blurs, so a post under one blur rule
    and one warn rule reads as ``warn``: the reader is told, rather than
    left to work out why the media is covered. Titles name every rule
    that hit -- a view that collapses the post shows them, and one that
    drops it never reads them.

    *now* judges whether a rule has expired. Omitted means no expiry
    check at all, so a snapshot resolved against its own window reads
    the same on every open; a live view passes the current time.
    """
    rules = _live_rules(status, context, now)
    if not rules:
        return None
    actions = [rule.get('filter_action') or DEFAULT_ACTION
               for rule in rules]
    titles = [rule.get('title') or '' for rule in rules]
    if HIDE in actions:
        action = HIDE
    elif all(seen == BLUR for seen in actions):
        action = BLUR
    else:
        action = DEFAULT_ACTION
    return {'action': action, 'titles': titles}


def strongest(status, context=HOME, now=None):
    """``(action, title)`` naming the rule that decided *status*, or None.

    The shape a row's filter bar wants: the action to act on, and one
    title to put in it.
    """
    found = resolve(status, context, now)
    if found is None:
        return None
    titles = found['titles']
    return found['action'], (titles[0] if titles else '')
