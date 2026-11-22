# SPDX-License-Identifier: GPL-3.0-or-later

"""Whether a row hides the "RE: <link>" line of a quote post.

A server appends that line to a quote post for clients that cannot show the
quote. Babble hides it when the row shows the quote as a card; this is the
one decision, read by the row (``blueskycard.wants_inline_dropped``) and by
Feed Chef's minimum length, which must count what the reader sees. No
widget, no settings: the caller says whether native Bluesky is on."""

from . import bskytext, statustext


def hidden(status, native_bluesky):
    """True when the "RE: …" line of *status* is shown as a card instead:
    a Mastodon quote, or (with the native Bluesky setting on) a card for a
    Bluesky post, or the line's own link to one. A poll or attachments
    suppress the card, and then the line stays."""
    if status.get('quote'):
        return True
    if status.get('poll') or status.get('media_attachments'):
        return False
    if not native_bluesky:
        return False
    card = status.get('card')
    if bskytext.card_post(card) and (card.get('title')
                                     or card.get('description')):
        return True
    url = statustext.quote_inline_url(status.get('content') or '')
    return bool(url and bskytext.post_ref(url))
