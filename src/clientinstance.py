# SPDX-License-Identifier: GPL-3.0-or-later
"""Instance capabilities: what this server allows.

The instance-info cache and every gate that reads it -- characters, poll
options, media limits, the version -- so a composer asks the client and
the client asks here. A mixin, so the transport keeps one object.
"""

import gi

gi.require_version('Soup', '3.0')

import json

from gi.repository import Gio, GLib  # noqa: E402

try:
    from .api import instance as api_instance  # noqa: E402
except ImportError:  # loaded as a top-level module by the standalone tests
    from api import instance as api_instance  # noqa: E402
# Mastodon's own default per-post attachment count, used when an
# instance does not advertise one (Status::MEDIA_ATTACHMENTS_LIMIT).
DEFAULT_MAX_MEDIA_ATTACHMENTS = 4
# Poll options when ``configuration.polls.max_options`` is unsaid.
DEFAULT_MAX_POLL_OPTIONS = 4
# Alt text length when ``description_limit`` is unsaid: the pre-4.4 value,
# the one an old server cannot refuse (4.4.0 raised it to 1500).
DEFAULT_DESCRIPTION_LIMIT = 420
# Characters a link counts for (``characters_reserved_per_url``).
DEFAULT_URL_CHARACTERS = 23


class InstanceMixin:
    def instance_info_async(self, callback=None, cancellable=None):
        """GET /api/v2/instance; the result is kept on
        ``self.instance_info`` by :meth:`instance_info_finish`.

        Concurrent callers share one in-flight request (ARCH-09). The
        shared HTTP call takes no caller cancellable: cancelling one
        waiter must not fail the answer for the others. Each waiter is
        completed on its own cancellable, or skipped when already
        cancelled."""
        if callback is not None:
            self._instance_info_waiters.append((callback, cancellable))
        if self.instance_info is not None:
            self._complete_instance_info_waiters(None)
            return None
        if self._instance_info_in_flight:
            return None
        self._instance_info_in_flight = True

        def loaded(client, result, _user_data=None):
            error = None
            try:
                client.instance_info_finish(result)
            except GLib.Error as failure:
                error = failure
            client._instance_info_in_flight = False
            client._complete_instance_info_waiters(error)

        return api_instance.info_async(self, callback=loaded,
                                       cancellable=None)

    def _complete_instance_info_waiters(self, error):
        waiters = self._instance_info_waiters
        self._instance_info_waiters = []
        payload = None
        if error is None and self.instance_info is not None:
            payload = GLib.Variant(
                's', json.dumps({'data': self.instance_info, 'link': ''}))
        for callback, cancellable in waiters:
            if cancellable is not None and cancellable.is_cancelled():
                continue
            task = Gio.Task.new(self, cancellable, callback)
            if error is not None:
                task.return_error(error)
            else:
                task.return_value(payload)

    def instance_info_finish(self, result):
        self.instance_info = self._finish(result)
        return self.instance_info

    def max_characters(self):
        """Post length limit of the instance, 500 until it is known."""
        info = self.instance_info or {}
        statuses = (info.get('configuration') or {}).get('statuses') or {}
        return statuses.get('max_characters') or 500

    def _media_configuration(self):
        """``configuration.media_attachments`` or {} when unsaid."""
        config = (self.instance_info or {}).get('configuration') or {}
        return config.get('media_attachments') or {}

    def max_media_attachments(self):
        """How many attachments one post may carry here.

        Mastodon's own default when the instance is silent about it:
        Status::MEDIA_ATTACHMENTS_LIMIT is 4, so an instance that raises
        or lowers it is honoured and one that says nothing is not
        blocked.
        """
        statuses = (self.instance_info or {}).get('configuration') or {}
        statuses = (statuses.get('statuses') or {})
        try:
            limit = int(statuses.get('max_media_attachments'))
        except (TypeError, ValueError):
            return DEFAULT_MAX_MEDIA_ATTACHMENTS
        return limit if limit > 0 else DEFAULT_MAX_MEDIA_ATTACHMENTS

    def poll_limits(self):
        """``configuration.polls`` as a dict: ``max_options`` (4 when
        unsaid, Mastodon's own default) and the three bounds the docs name
        (docs.joinmastodon.org/entities/Instance/#polls),
        ``max_characters_per_option``, ``min_expiration`` and
        ``max_expiration``, each None when the server does not say. None
        means no bound to enforce, which is the safe direction to fail in:
        the server answers a poll it refuses. A value that is not a
        positive integer counts as unsaid, and so does a ``max_options``
        under two, which no poll can use.
        """
        polls = (((self.instance_info or {}).get('configuration') or {})
                 .get('polls') or {})

        def positive(name):
            value = polls.get(name)
            if isinstance(value, bool) or not isinstance(value, int) \
                    or value <= 0:
                return None
            return value
        options = positive('max_options')
        return {
            'max_options': options if options and options >= 2
            else DEFAULT_MAX_POLL_OPTIONS,
            'max_characters_per_option': positive('max_characters_per_option'),
            'min_expiration': positive('min_expiration'),
            'max_expiration': positive('max_expiration'),
        }

    def media_description_limit(self):
        """Longest alt text one attachment takes.

        ``configuration.media_attachments.description_limit`` (docs: 1500
        by default since 4.4.0). A server that says nothing gets the older
        420, because it may be a pre-4.4 Mastodon that refuses anything
        longer with a 422; the lower number is the one that cannot fail.
        """
        value = self._media_configuration().get('description_limit')
        if isinstance(value, bool) or not isinstance(value, int) \
                or value <= 0:
            return DEFAULT_DESCRIPTION_LIMIT
        return value

    def characters_reserved_per_url(self):
        """How many characters a link counts for in a post
        (``configuration.statuses.characters_reserved_per_url``), 23 until
        the instance says."""
        info = self.instance_info or {}
        statuses = (info.get('configuration') or {}).get('statuses') or {}
        value = statuses.get('characters_reserved_per_url')
        if isinstance(value, bool) or not isinstance(value, int) \
                or value <= 0:
            return DEFAULT_URL_CHARACTERS
        return value

    def media_size_limits(self):
        """``(image_bytes, video_bytes)`` from the instance, either None
        where it advertised nothing.

        Both are enforced server-side: MediaAttachment validates a file
        against VIDEO_LIMIT for video, gifv and audio and IMAGE_LIMIT
        otherwise, and strictly, so a file of exactly the limit is
        refused. None means no limit to enforce, which is the safe
        direction to fail in.
        """
        media = self._media_configuration()

        def limit(name):
            try:
                value = int(media[name])
            except (KeyError, TypeError, ValueError):
                return None
            return value if value > 0 else None
        return limit('image_size_limit'), limit('video_size_limit')

    def supported_media_types(self):
        """``configuration.media_attachments.supported_mime_types``, or
        None when there is nothing worth filtering a file chooser on.

        None also covers the instance that advertises the single type
        ``application/octet-stream``: as a chooser filter that hides
        every file a reader actually has.
        """
        advertised = self._media_configuration().get('supported_mime_types')
        if not isinstance(advertised, list):
            return None
        types = [t for t in advertised
                 if isinstance(t, str) and t != 'application/octet-stream']
        return types or None

    def mastodon_api_version(self):
        """The instance's ``api_versions.mastodon``, or None when it says
        nothing at all.

        Three things are conflated by reading this as an int and getting
        0 on a server that does not carry the key: a Mastodon older than
        4.3 (where the key first appears), a Mastodon that old and then
        some, and a server that is not Mastodon at all. A None keeps
        those apart -- "no answer" versus "answered 0".
        """
        versions = (self.instance_info or {}).get('api_versions') or {}
        try:
            return int(versions.get('mastodon'))
        except (TypeError, ValueError):
            return None

    def is_supported(self, min_version, when_unknown=False):
        """Whether the instance has the Mastodon *min_version* and later
        feature the caller is about to use.

        The answer is None when the server says nothing about
        api_versions.mastodon: without it, the feature is unproven rather
        than absent. *when_unknown* is what a caller does with that, and
        lives here rather than at every call site so a caller states its
        policy once, in the natural reading order, instead of spelling
        out ``is True`` and a comment.

        A Mastodon feature a fork does not ship, a fork feature Mastodon
        does not, and an old Mastodon are three different answers, and
        only the first two are ones the instance can tell us about.
        """
        version = self.mastodon_api_version()
        if version is None:
            return when_unknown
        return version >= min_version

