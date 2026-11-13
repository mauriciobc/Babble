# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer mode transitions as data.

Builders return a frozen :class:`Transition`; the composer view applies it
through :meth:`BabbleComposeView.begin`. This module is standard-library
only: no ``gi``, no gettext, no sibling app imports.
"""

from dataclasses import dataclass

# Visibility codes the composer can restore. Equal to
# ``entities.VISIBILITIES``; duplicated so this module stays stdlib-only.
_VISIBILITIES = frozenset(('public', 'unlisted', 'private', 'direct'))

# Stack / field sentinels -------------------------------------------------

class _Sentinel:
    __slots__ = ('name',)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return self.name


KEEP = _Sentinel('KEEP')
PUSH = _Sentinel('PUSH')
PUSH_IF_NOT_EMPTY = _Sentinel('PUSH_IF_NOT_EMPTY')
CLEAR = _Sentinel('CLEAR')


class UnsupportedSettings(Exception):
    """Scheduled payload the composer cannot restore."""


@dataclass(frozen=True)
class Transition:
    """One begin: every field is :data:`KEEP` or the value to write."""

    stack: object
    text: object
    cw: object
    visibility: object
    language: object
    media: object
    poll: object
    in_reply_to_id: object
    quoted_status_id: object
    editing_id: object
    scheduled: object
    scheduled_fields: object
    thread_lookup: object


def scheduled_content(scheduled, *, max_poll_options):
    """Shape a scheduled-status payload into composer fields.

    Raises :class:`UnsupportedSettings` when the server returned settings
    the composer cannot restore.
    """
    def unsupported():
        raise UnsupportedSettings(
            'The server returned unsupported post settings.')

    if not isinstance(scheduled, dict) or not isinstance(scheduled.get('id'), str) \
            or not scheduled['id']:
        unsupported()
    params = scheduled.get('params')
    if params is None:
        params = {}
    if not isinstance(params, dict):
        unsupported()
    content = {}
    for key in ('text', 'spoiler_text'):
        value = params.get(key)
        if value is None:
            value = ''
        if not isinstance(value, str):
            unsupported()
        content[key] = value
    visibility = params.get('visibility')
    if visibility is None:
        visibility = 'public'
    if visibility not in _VISIBILITIES:
        unsupported()
    content['visibility'] = visibility
    language = params.get('language')
    if language is not None and not isinstance(language, str):
        unsupported()
    content['language'] = language
    for key in ('in_reply_to_id', 'quoted_status_id'):
        value = params.get(key)
        if key == 'in_reply_to_id' and type(value) is int and value > 0:
            value = str(value)
        if value is not None and (not isinstance(value, str) or not value):
            unsupported()
        content[key] = value
    sensitive = params.get('sensitive')
    if sensitive is not None and not isinstance(sensitive, bool):
        unsupported()
    policy = params.get('quote_approval_policy')
    if policy is not None and policy not in ('public', 'followers', 'nobody'):
        unsupported()
    content.update(sensitive=sensitive, quote_approval_policy=policy)
    attachments = scheduled.get('media_attachments')
    media_ids = params.get('media_ids')
    if attachments is None:
        attachments = []
    if media_ids is None:
        media_ids = []
    if not isinstance(attachments, list) or not isinstance(media_ids, list):
        unsupported()
    ids = []
    for attachment in attachments:
        if not isinstance(attachment, dict):
            unsupported()
        value = attachment.get('id')
        if not isinstance(value, str) or not value:
            unsupported()
        ids.append(value)
    if any(not isinstance(value, str) or not value for value in media_ids):
        unsupported()
    content['media_ids'] = ids or list(media_ids)
    poll = params.get('poll')
    if poll is not None:
        if not isinstance(poll, dict):
            unsupported()
        options = poll.get('options')
        expiry = poll.get('expires_in')
        if not isinstance(options, list) \
                or not 2 <= len(options) <= max_poll_options \
                or any(not isinstance(o, str) or not o.strip() for o in options) \
                or len({o.strip() for o in options}) != len(options) \
                or type(expiry) is not int or expiry <= 0:
            unsupported()
        flags = {key: poll.get(key, False) for key in ('multiple', 'hide_totals')}
        if any(not isinstance(value, bool) for value in flags.values()):
            unsupported()
        poll = dict(options=list(options), expires_in=expiry, **flags)
    content['poll'] = poll
    return content


def _status_draft(status, *, show_media):
    """Shared edit / redraft field values from a status dict."""
    source = dict(status)
    attachments = [a for a in source.get('media_attachments') or []
                   if a.get('id')]
    if show_media:
        media = (True, (), tuple(attachments))
    else:
        media = (True, tuple(a['id'] for a in attachments), ())
    spoiler = source.get('spoiler_text') or ''
    cw = (bool(spoiler), spoiler)
    fields = {}
    if source.get('sensitive'):
        fields['sensitive'] = True
    return Transition(
        stack=CLEAR,
        text=source.get('text') or '',
        cw=cw,
        visibility=source.get('visibility') or 'public',
        language=KEEP,
        media=media,
        poll=CLEAR,
        in_reply_to_id=None,
        quoted_status_id=KEEP,
        editing_id=KEEP,
        scheduled=KEEP,
        scheduled_fields=fields or KEEP,
        thread_lookup=None,
    )


def reply(status_id, handle, visibility='public', parent=None):
    """Push the current draft (if any) and prefill a reply."""
    new_message = status_id is None and not handle
    if new_message:
        stack = PUSH_IF_NOT_EMPTY
        text = KEEP
    else:
        stack = PUSH
        text = '' if not handle else '@' + handle + ' '
    cw = KEEP
    if status_id is not None and parent is not None:
        parent_cw = parent.get('spoiler_text') or ''
        cw = (True, parent_cw) if parent_cw else (False, '')
    thread = None
    if status_id is not None:
        thread = (str(status_id), None, False)
    return Transition(
        stack=stack,
        text=text,
        cw=cw,
        visibility=visibility,
        language=KEEP,
        media=KEEP,
        poll=KEEP,
        in_reply_to_id=(str(status_id) if status_id is not None else None),
        quoted_status_id=KEEP,
        editing_id=KEEP,
        scheduled=KEEP,
        scheduled_fields=KEEP,
        thread_lookup=thread,
    )


def mention(account, direct=False):
    """Prefill a mention of *account* (``direct`` for a DM)."""
    acct = account.get('acct') or account.get('username') or ''
    return Transition(
        stack=PUSH,
        text='@%s ' % acct,
        cw=KEEP,
        visibility=('direct' if direct else KEEP),
        language=KEEP,
        media=KEEP,
        poll=KEEP,
        in_reply_to_id=None,
        quoted_status_id=KEEP,
        editing_id=KEEP,
        scheduled=KEEP,
        scheduled_fields=KEEP,
        thread_lookup=None,
    )


def edit(status):
    """Load an existing post into the composer for a PUT."""
    transition = _status_draft(status, show_media=True)
    editing_id = str(status.get('id') or '') or None
    return Transition(
        stack=transition.stack,
        text=transition.text,
        cw=transition.cw,
        visibility=transition.visibility,
        language=transition.language,
        media=transition.media,
        poll=transition.poll,
        in_reply_to_id=transition.in_reply_to_id,
        quoted_status_id=transition.quoted_status_id,
        editing_id=editing_id,
        scheduled=transition.scheduled,
        scheduled_fields=transition.scheduled_fields,
        thread_lookup=(editing_id, status, True) if editing_id else None,
    )


def redraft(status):
    """Refill the composer from a just-deleted post."""
    return _status_draft(status, show_media=False)


def quote(status):
    """Quote *status*: the server prepends the quoted post."""
    return Transition(
        stack=CLEAR,
        text=KEEP,
        cw=KEEP,
        visibility=KEEP,
        language=KEEP,
        media=KEEP,
        poll=KEEP,
        in_reply_to_id=None,
        quoted_status_id=str(status.get('id') or '') or None,
        editing_id=KEEP,
        scheduled=KEEP,
        scheduled_fields=KEEP,
        thread_lookup=None,
    )


def scheduled(payload, *, max_poll_options, at, on_done=None):
    """Load a scheduled post or draft into the composer.

    *at* is the scheduled time to show (the draft sentinel when editing a
    server-side draft). Raises :class:`UnsupportedSettings` before any
    field is produced when the payload cannot be restored.
    """
    params = scheduled_content(payload, max_poll_options=max_poll_options)
    spoiler = params.get('spoiler_text') or ''
    fields = {}
    for key in ('sensitive', 'quote_approval_policy'):
        if params[key] is not None:
            fields[key] = params[key]
    poll = params['poll']
    if poll is not None:
        fields['poll_flags'] = {
            key: poll[key] for key in ('multiple', 'hide_totals')}
        poll_value = poll
    else:
        poll_value = CLEAR
    return Transition(
        stack=CLEAR,
        text=params.get('text') or '',
        cw=(bool(spoiler), spoiler),
        visibility=params.get('visibility') or 'public',
        language=params['language'],
        media=(True, tuple(params['media_ids']), ()),
        poll=poll_value,
        in_reply_to_id=params.get('in_reply_to_id'),
        quoted_status_id=params['quoted_status_id'],
        editing_id=KEEP,
        scheduled=(payload['id'], at, on_done),
        scheduled_fields=fields or KEEP,
        thread_lookup=None,
    )


def prefill(text):
    """Start a post with *text*, as shared from outside the app."""
    return Transition(
        stack=PUSH_IF_NOT_EMPTY,
        text=text,
        cw=KEEP,
        visibility=KEEP,
        language=KEEP,
        media=KEEP,
        poll=KEEP,
        in_reply_to_id=None,
        quoted_status_id=KEEP,
        editing_id=KEEP,
        scheduled=KEEP,
        scheduled_fields=KEEP,
        thread_lookup=None,
    )
