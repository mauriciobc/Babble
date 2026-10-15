# SPDX-License-Identifier: GPL-3.0-or-later
"""Status, Account, Notification and Relationship.

These are the four API objects the hot paths read as raw dicts. They stay
dicts — every existing ``.get`` keeps working — and gain a type, so the
facts that used to be copied into private tables live in one place:
visibility codes, quote-policy codes, and the keys an edit merges back
onto a row.

``lift`` returns the typed value. A matching dict is copied into its
class — a built-in dict cannot change class in place — and the caller
uses the return. Lists and dicts that only *contain* the four (a search
result, a streaming payload) are updated to point at the typed copies.
"""


# PostVisibility, as the server names the four values.
VISIBILITIES = ('public', 'unlisted', 'private', 'direct')

# source[quote_policy] / quote_approval.automatic, same three codes.
QUOTE_POLICIES = ('public', 'followers', 'nobody')

# Fields a status action (favourite, boost, edit, poll vote) copies onto
# the row it already holds. One list: an action that forgets a key here
# forgets it everywhere.
STATUS_MERGE_KEYS = (
    'favourited', 'reblogged', 'bookmarked', 'muted', 'pinned',
    'favourites_count', 'reblogs_count', 'replies_count', 'quotes_count',
    'edited_at', 'content', 'spoiler_text', 'sensitive', 'emojis',
    'media_attachments', 'poll', 'quote', 'quote_approval', 'card')


class Account(dict):
    """One account. ``id``, ``acct`` and ``username`` are the fields every
    caller agrees on; the rest stays available through ``.get``."""

    @property
    def id(self):
        return self.get('id')

    @property
    def acct(self):
        return self.get('acct') or ''

    @property
    def username(self):
        return self.get('username') or ''


class Status(dict):
    """One status. A boost's ``reblog`` and a quote's ``quote`` are
    statuses; ``account`` is an account. All three are lifted with it."""

    @property
    def id(self):
        return self.get('id')

    @property
    def account(self):
        account = self.get('account')
        return account if isinstance(account, dict) else {}

    @property
    def reblog(self):
        reblog = self.get('reblog')
        return reblog if isinstance(reblog, dict) else None

    @property
    def visibility(self):
        value = self.get('visibility') or 'public'
        return value if value in VISIBILITIES else 'public'

    def quote_policy(self):
        """``public``, ``followers`` or ``nobody``, from
        ``quote_approval.automatic`` — the same reading the composer and
        the profile used to spell out separately."""
        automatic = (self.get('quote_approval') or {}).get('automatic') or []
        for code in QUOTE_POLICIES:
            if code in automatic:
                return code
        return 'nobody'


class Notification(dict):
    """One notification. ``account`` is the actor and ``status`` the post,
    when the type carries one."""

    @property
    def id(self):
        return self.get('id')

    @property
    def type(self):
        return self.get('type') or ''

    @property
    def account(self):
        account = self.get('account')
        return account if isinstance(account, dict) else {}

    @property
    def status(self):
        status = self.get('status')
        return status if isinstance(status, dict) else None


class Relationship(dict):
    """The viewer's relationship to one account."""

    @property
    def id(self):
        return self.get('id')

    @property
    def following(self):
        return bool(self.get('following'))

    @property
    def followed_by(self):
        return bool(self.get('followed_by'))

    @property
    def blocking(self):
        return bool(self.get('blocking'))

    @property
    def muting(self):
        return bool(self.get('muting'))

    @property
    def endorsed(self):
        return bool(self.get('endorsed'))


def attachment_still_url(attachment):
    """Still-frame URL Gdk can decode for a media attachment.

    Still images decode from ``url``; video and gifv streams need
    ``preview_url``. One rule, so a tile cannot prefer the preview of an
    image while another prefers its full URL."""
    if not isinstance(attachment, dict):
        return None
    if attachment.get('type') == 'image':
        return attachment.get('url') or attachment.get('preview_url')
    return attachment.get('preview_url') or attachment.get('url')


_TYPES = (Status, Account, Notification, Relationship)


def _classify(data):
    """Which of the four *data* is, or None when it is some other object.

    Account before Status: an account has ``username`` and ``acct`` and
    no post body. Status before Notification: a post has ``content``.
    Relationship is the flags object, which has neither.
    """
    if 'username' in data and 'acct' in data and 'id' in data:
        return Account
    if 'content' in data and 'account' in data and 'created_at' in data \
            and 'id' in data:
        return Status
    if data.get('type') and 'account' in data and 'created_at' in data \
            and 'id' in data and 'username' not in data:
        return Notification
    if 'following' in data and 'followed_by' in data and 'id' in data:
        return Relationship
    return None


def lift(data):
    """Retype Status, Account, Notification and Relationship values in
    *data*, walking lists and plain dicts that contain them.

    The same object comes back. A value that is already lifted is left
    alone, so calling this twice is free."""
    if isinstance(data, _TYPES):
        return data
    if type(data) is list:
        for index, item in enumerate(data):
            lifted = lift(item)
            if lifted is not item:
                data[index] = lifted
        return data
    if type(data) is not dict:
        return data
    kind = _classify(data)
    # A built-in dict cannot be retyped in place, so a match is copied
    # into the subclass. Callers have to use the return value. Nested
    # values are lifted on the copy, which leaves the original dict
    # untouched.
    target = kind(data) if kind is not None else data
    for key, value in list(target.items()):
        lifted = lift(value)
        if lifted is not value:
            target[key] = lifted
    return target
