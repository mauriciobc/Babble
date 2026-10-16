# SPDX-License-Identifier: GPL-3.0-or-later
"""One posting round's media uploads.

The composer widget decides what is being posted and shows errors. This
object owns the async job: the generation guard, the cancellable, the
processing poll and when enough uploads have landed to send. Its only
contact with the widget is the row it was handed (``uploaded_id``,
``path``, ``content_type``, ``alt_entry``) and two callbacks.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Soup', '3.0')

from .i18n import gettext as _  # noqa: E402

from gi.repository import Gio, GLib  # noqa: E402

# An instance may take a few seconds to transcode an upload. Poll
# GET /media/:id until it is ready, but never longer than this: a post
# blocked on a stuck upload is worse than a clear failure.
PROCESSING_INTERVAL_MS = 1500
PROCESSING_ATTEMPTS = 80


def _cancelled(error):
    """True when *error* is the cancellation of a request we abandoned."""
    return error.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED)


def uses_video_size_limit(mime):
    """Whether *mime* is held to the instance's video size limit.

    This is the server's own split: MediaAttachment validates the file
    against VIDEO_LIMIT for anything it classes as video or audio and
    IMAGE_LIMIT for an image, strictly, so a file of exactly the limit is
    refused there too.

    There is no third case hiding here. Mastodon makes an animated GIF a
    gifv only *after* the upload -- the client sends ``image/gif`` and
    the server transcodes it to mp4 -- so no client ever sends a gifv
    MIME, and ``image/gif`` is an image here exactly as it is there. It
    is worth saying because the enum reads like a third bucket, and
    building a rule for it would be building for a request the server
    rejects: content_type is validated against IMAGE_MIME_TYPES +
    VIDEO_MIME_TYPES + AUDIO_MIME_TYPES, and image/gifv is in none.
    """
    return bool(mime) and mime.lower().startswith(('video/', 'audio/'))


def size_limit_for(mime, limits):
    """The limit *mime* of *limits* breaks, or None.

    *limits* is :meth:`MastodonClient.media_size_limits`. The comparison
    is ``>=`` because the server's validation is ``less_than``, so a
    file of exactly the limit is refused there too.
    """
    image_limit, video_limit = limits
    limit = video_limit if uses_video_size_limit(mime) else image_limit
    return limit if limit is not None and limit > 0 else None


def human_size(limit):
    """*limit* bytes as the reader would say it, e.g. '16 MB'."""
    mb = limit / (1024.0 * 1024.0)
    if mb >= 1:
        return '%.0f MB' % mb if mb == int(mb) else '%.2f MB' % mb
    return '%.0f kB' % (limit / 1024.0)


def attachment_refusal(client, mime, size, name=None, limits=None):
    """Why *client* will not take this attachment, or None.

    *size* is the file's length in bytes, from a metadata query rather
    than a read: the point of asking before the upload is that an
    oversized file is never pulled into memory only to earn a 422.
    *name* is the basename to name in the message, *limits* is what the
    client advertises unless a test passes its own.
    """
    limits = client.media_size_limits() if limits is None else limits
    limit = size_limit_for(mime, limits)
    if limit is None or size < limit:
        return None
    # Translators: %s is a file name, then the largest size this server
    # accepts for this kind of file, e.g. "99 MB".
    return _('“%s” is too large for this server (%s is the limit)') \
        % (name or '', human_size(limit))


class UploadRound:
    """Uploads for a single click of Post.

    ``on_failure(message)`` is the widget's toast. ``on_ready()`` is
    called once, when every expected upload has an id and none failed,
    which is when the widget sends the post.
    """

    def __init__(self, client, on_failure, on_ready):
        self.client = client
        self.on_failure = on_failure
        self.on_ready = on_ready
        self.results = []
        self.failures = 0
        self.generation = 0
        self.expected = 0
        self.cancellable = None
        self._processing_poll = {}

    def begin(self, expected):
        """Start a round of *expected* uploads, cancelling the previous."""
        self.cancel()
        self.generation += 1
        self.results = []
        self.failures = 0
        self.expected = expected
        self.cancellable = Gio.Cancellable()

    def abandon(self):
        """Drop the round. A later reply from it sees a new generation
        and does nothing — cancelling alone would still deliver an error
        the reply would act on."""
        self.generation += 1
        self.cancel()
        self.results = []
        self.failures = 0
        self.expected = 0

    def clear_results(self):
        """Forget ids already collected, without abandoning the round.
        A thread chains the next post on the same generation."""
        self.results = []
        self.failures = 0

    def cancel(self):
        """Stop whatever this round still had in flight."""
        if self.cancellable is not None:
            self.cancellable.cancel()
        self.cancellable = None
        for source in self._processing_poll.values():
            GLib.source_remove(source)
        self._processing_poll.clear()

    def upload_one(self, row):
        # Ask the instance what it takes before touching the file: the
        # size comes from a metadata query, so an attachment this server
        # would refuse is refused here too, without ever being read into
        # memory. It is up to a server to enforce it; doing it here only
        # means the reader is told which file and why.
        try:
            info = Gio.File.new_for_path(row.path).query_info(
                'standard::size,standard::content-type',
                Gio.FileQueryInfoFlags.NONE)
        except GLib.Error as error:
            self.fail(error.message)
            return
        refusal = attachment_refusal(
            self.client, row.content_type, info.get_size(),
            GLib.path_get_basename(row.path))
        if refusal is not None:
            self.fail(refusal)
            return
        # Gio reads the file into a GLib.Bytes itself, in C. handle.read()
        # plus the GLib.Bytes.new() inside the client would hold a second
        # full copy of the attachment on the way out, and Mastodon lets a
        # video be the better part of 100 MB.
        try:
            data = Gio.File.new_for_path(row.path).load_bytes(None)[0]
        except GLib.Error as error:
            self.fail(error.message)
            return
        generation = self.generation
        if self.cancellable is None:
            self.cancellable = Gio.Cancellable()
        self.client.media_upload_async(
            data, GLib.path_get_basename(row.path), row.content_type,
            description=row.alt_entry.get_text() or None,
            callback=self._uploaded(row, generation),
            cancellable=self.cancellable)

    def _uploaded(self, row, generation):
        def finished(client, result, _user_data=None):
            if generation != self.generation:
                return
            self._on_uploaded(row, result)
        return finished

    def _on_uploaded(self, row, result):
        try:
            data = self.client.media_upload_finish(result)
        except GLib.Error as error:
            # A cancelled upload belongs to a round the user abandoned,
            # not to a failure worth a toast.
            if _cancelled(error):
                return
            self.fail(error.message)
            return
        media_id = data.get('id')
        if not data.get('url'):
            # Nothing decoded yet: the instance would reject the post,
            # so wait for the attachment to finish processing first.
            self._await_processing(row, media_id)
            return
        self._note_ready(row, media_id, data)

    def _await_processing(self, row, media_id, attempt=0):
        """Poll GET /media/:id until the instance finishes processing."""
        if media_id is None:
            self.fail(_('The server did not accept the media'))
            return
        generation = self.generation

        def check(client, result, _user_data=None):
            if generation != self.generation:
                return
            try:
                media = client.media_status_finish(result)
            except GLib.Error as error:
                # A cancelled request is this round going away, not the
                # instance being slow: retrying it would keep polling a
                # draft the user already discarded.
                if _cancelled(error):
                    return
                if attempt + 1 >= PROCESSING_ATTEMPTS:
                    self.fail(error.message)
                    return
                self._schedule(row, media_id, attempt + 1, generation)
                return
            if media.get('url'):
                self._note_ready(row, media_id, media)
                return
            if attempt + 1 >= PROCESSING_ATTEMPTS:
                self.fail(_('The server is still processing the media'))
                return
            self._schedule(row, media_id, attempt + 1, generation)

        self.client.media_status_async(
            media_id, callback=check, cancellable=self.cancellable)

    def _schedule(self, row, media_id, attempt, generation):
        source = GLib.timeout_add(
            PROCESSING_INTERVAL_MS,
            self._run_check, row, media_id, attempt, generation)
        self._processing_poll[media_id] = source

    def _run_check(self, row, media_id, attempt, generation):
        self._processing_poll.pop(media_id, None)
        if generation != self.generation:
            return False
        self._await_processing(row, media_id, attempt)
        return False

    def _note_ready(self, row, media_id, data):
        row.uploaded_id = media_id
        self.results.append(data)
        self._maybe_ready()

    def _maybe_ready(self):
        if len(self.results) + self.failures == self.expected \
                and not self.failures:
            self.on_ready()

    def fail(self, message):
        self.failures += 1
        self.on_failure(message)
