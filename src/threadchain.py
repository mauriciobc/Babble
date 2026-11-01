# SPDX-License-Identifier: GPL-3.0-or-later
"""Where a post sits in a chain of its author's own posts (a thread).

Pure data helpers: no widgets, so they can be tested without a display.
"""

from dataclasses import dataclass

from .statustext import parse_content


@dataclass(frozen=True)
class ThreadPosition:
    index: int          # 1-based position of the given status in the chain
    total: int          # chain length
    previous: dict | None  # the status before it in the chain, or None


def _id(status):
    value = status.get('id') if isinstance(status, dict) else None
    return '' if value is None else str(value)


def _parent_id(status):
    value = status.get('in_reply_to_id')
    return '' if value is None else str(value)


def _author_id(status):
    account = status.get('account')
    if not isinstance(account, dict) or account.get('id') is None:
        return None
    return str(account['id'])


def own_thread(status, ancestors, descendants, account_id):
    """Position of *status* in the chain of *account_id*'s own posts.
    None when status is not the account's, or the chain has length 1."""
    if account_id is None or _author_id(status) != str(account_id):
        return None
    account_id = str(account_id)
    by_id = {_id(s): s for s in ancestors or () if isinstance(s, dict)}

    previous = None
    above = 0
    current = status
    while True:
        parent = by_id.get(_parent_id(current))
        if parent is None or _author_id(parent) != account_id:
            break
        if previous is None:
            previous = parent
        above += 1
        current = parent
        if above > len(by_id):  # a cycle in malformed data
            break

    below = 0
    tail = _id(status)
    seen = {tail}
    candidates = [s for s in descendants or ()
                  if isinstance(s, dict) and _author_id(s) == account_id]
    while True:
        children = [s for s in candidates
                    if _parent_id(s) == tail and _id(s) not in seen]
        if not children:
            break
        child = min(children, key=lambda s: (s.get('created_at') or '',
                                             _id(s)))
        below += 1
        tail = _id(child)
        seen.add(tail)

    total = above + 1 + below
    if total < 2:
        return None
    return ThreadPosition(above + 1, total, previous)


def plain_text(status):
    """Status HTML content as plain text (via statustext.parse_content)."""
    text = []
    space = False  # the source had whitespace after the last segment
    for segment in parse_content(status.get('content') or ''):
        piece = segment.get('text') or ''
        if piece == '\n':
            # A paragraph break is a blank line, a line break just a newline.
            piece = '\n' if segment.get('br') else '\n\n'
            space = False
            text.append(piece)
            continue
        if (space or segment.get('sp_before')) and text and piece \
                and not text[-1].endswith('\n'):
            text.append(' ')
        text.append(piece)
        space = bool(segment.get('sp_after'))
    return ''.join(text).strip()


def preview_tail(text, limit=140):
    """Last ~limit chars of text cut at a word boundary, prefixed with '…'
    when shortened; '' for empty/whitespace text. Newlines collapsed to spaces."""
    text = ' '.join((text or '').split())
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    # Cut was mid-word unless it fell right after a space.
    if text[-limit - 1] != ' ':
        space = tail.find(' ')
        tail = tail[space + 1:] if space >= 0 else tail
    return '…' + tail.lstrip()
