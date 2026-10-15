# SPDX-License-Identifier: GPL-3.0-or-later

"""Unsent drafts, as values.

A draft is Babble's own data with a schema we wrote, so it is the one
place where a real object beats a JSON dict outright: :class:`Draft`
*is* the schema, and :meth:`Draft.parse` is the only thing that ever has
to know what malformed looks like.

That replaces the ``isinstance`` ritual that used to guard the restore
path. There is no longer a validator and a consumer that can disagree
about the field set -- a draft either parsed, or there is no draft.
"""

import dataclasses
import json
import typing


# How many unsent drafts are kept, in all.
MAX_LOCAL_DRAFTS = 5


@dataclasses.dataclass(frozen=True)
class Attachment:
    """One staged upload: where it came from, and its alt text."""
    path: str
    alt: str

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get('path'), str):
            return None
        if not isinstance(data.get('alt'), str):
            return None
        return cls(path=data['path'], alt=data['alt'])

    def as_dict(self):
        return {'path': self.path, 'alt': self.alt}


@dataclasses.dataclass(frozen=True)
class Poll:
    """A poll's state: whether it is live, its choices, and its expiry."""
    active: bool
    options: typing.List[str]
    expires: int

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get('active'), bool):
            return None
        if not isinstance(data.get('options'), list):
            return None
        if not isinstance(data.get('expires'), int):
            return None
        return cls(active=data['active'], options=list(data['options']),
                   expires=data['expires'])

    def as_dict(self):
        return {'active': self.active, 'options': list(self.options),
                'expires': self.expires}


@dataclasses.dataclass(frozen=True)
class Draft:
    """Everything the composer needs to put a message back together.

    ``key`` is the ``instance#id`` of the account the draft belongs to.
    Drafts stay keyed with the account because ids like
    ``in_reply_to_id`` inside them are server-local: restoring one on
    another instance would answer an unrelated post.
    """
    text: str
    visibility: int
    language: int
    cw_visible: bool
    cw_text: str
    attachments: typing.List[Attachment]
    poll: Poll
    in_reply_to_id: typing.Optional[str] = None
    key: str = ''
    saved_at: str = ''

    @classmethod
    def parse(cls, data, key=''):
        """The draft *data* is, or None when it is not one.

        Every field the composer reads has to be there and of the right
        type. Anything short of that is not a draft worth restoring: the
        caller skips it and it ages out of the list, rather than
        half-restoring a message.
        """
        if not isinstance(data, dict):
            return None
        if not isinstance(data.get('text'), str):
            return None
        if not isinstance(data.get('visibility'), int):
            return None
        if not isinstance(data.get('language'), int):
            return None
        if not isinstance(data.get('cw_visible'), bool):
            return None
        if not isinstance(data.get('cw_text'), str):
            return None
        if not isinstance(data.get('attachments'), list):
            return None
        if 'in_reply_to_id' not in data:
            return None
        poll = Poll.parse(data.get('poll'))
        if poll is None:
            return None
        attachments = [Attachment.parse(a) for a in data['attachments']]
        if any(a is None for a in attachments):
            return None
        return cls(text=data['text'], visibility=data['visibility'],
                   language=data['language'], cw_visible=data['cw_visible'],
                   cw_text=data['cw_text'], attachments=attachments,
                   poll=poll, in_reply_to_id=data['in_reply_to_id'],
                   key=str(data.get('key') or key),
                   saved_at=str(data.get('saved_at') or ''))

    @classmethod
    def parse_raw(cls, raw, key=''):
        """The draft a stored string is, or None."""
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        return cls.parse(data, key)

    def as_dict(self):
        return {
            'text': self.text,
            'visibility': self.visibility,
            'language': self.language,
            'cw_visible': self.cw_visible,
            'cw_text': self.cw_text,
            'attachments': [a.as_dict() for a in self.attachments],
            'poll': self.poll.as_dict(),
            'in_reply_to_id': self.in_reply_to_id,
        }

    def as_stored(self, key, saved_at):
        """The JSON to keep in GSettings for *key* at *saved_at*.

        The raw string is what a later removal matches on, so an entry
        never depends on re-serializing the same way.
        """
        return json.dumps(dict(self.as_dict(), key=key, saved_at=saved_at))


def stored_drafts(raws, key):
    """The ``(raw, draft)`` pairs in *raws* that belong to *key*.

    ``raw`` is the string the entry is stored as, which is what a removal
    has to match on, so nothing here depends on re-serializing the same
    way.

    The list is kept in its stored order, which is newest first because a
    save prepends. Malformed entries are skipped rather than deleted: one
    Babble could not read is not evidence that it should be thrown away,
    and the setting is capped anyway.
    """
    drafts = []
    for raw in raws:
        draft = Draft.parse_raw(raw, key)
        if draft is not None and draft.key == key:
            drafts.append((raw, draft))
    return drafts


def store_local_draft(settings, raw, previous_raw=None):
    """Prepend *raw* to ``local-drafts``, drop *previous_raw*, cap the list."""
    kept = [raw]
    for entry in settings.get_strv('local-drafts'):
        if entry not in (previous_raw, raw):
            kept.append(entry)
    settings.set_strv('local-drafts', kept[:MAX_LOCAL_DRAFTS])


def drop_local_draft(settings, raw):
    """Remove one stored draft string from ``local-drafts``."""
    if raw is None:
        return
    settings.set_strv(
        'local-drafts',
        [entry for entry in settings.get_strv('local-drafts') if entry != raw])
