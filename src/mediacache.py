# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')


from gi.repository import (Gdk, GdkPixbuf, GLib, GObject, Gio,  # noqa: E402
                           Graphene, Gtk)

from . import imagecache  # noqa: E402
from .textemoji import (InlineEmojiText)

"""Avatar and media decode, and the two texture caches."""
from .timelinebase import AVATAR_DECODE_SIZE, MEDIA_TEXTURE_CACHE_SIZE


class AvatarCache:
    """Shares avatar textures across rows; one session, one HTTP hit.

    A thin name over the shared store so callers can keep saying
    ``AvatarCache.load``; the LRU, the session, the coalescing and the
    failure handling all live in imagecache.
    """

    # Bounded: a long session on a big timeline meets hundreds of avatars,
    # and decoded textures are expensive to keep.
    store = imagecache.Store(imagecache.AVATAR_CACHE_SIZE)

    @classmethod
    def _fetch(cls):
        if cls._fetcher is None:
            cls._fetcher = imagecache.Fetcher(
                cls.store,
                # Instances serve 400px avatars and the largest one shown
                # is 96; decoded at full size, a full avatar cache held
                # ~156 MB.
                lambda data, key, cancellable, done: _decode_async(
                    data, (AVATAR_DECODE_SIZE,) * 2, cancellable,
                    lambda texture, error: done(
                        texture, error and error.message)),
                on_failure=log_media_failure)
        return cls._fetcher

    _fetcher = None

    @classmethod
    def session(cls):
        """The shared Soup session. Kept because profile.py and
        textemoji.py reach for it; the fetches themselves all go through
        imagecache.Fetcher now."""
        return imagecache.session()

    @classmethod
    def load(cls, avatar, account):
        """Fill *avatar* with the cached or freshly fetched image."""
        url = account.get('avatar') or account.get('avatar_static') or ''
        avatar.set_custom_image(None)
        if not url:
            return

        def arrived(texture, _reason):
            # A failure leaves the avatar on its initials, its own
            # placeholder, and uncached so a later row retries.
            if texture is not None:
                avatar.set_custom_image(texture)

        cls._fetch().fetch(url, arrived)


class StatusBody(InlineEmojiText):
    """Post body through the shared emoji/text machinery: :shortcode:
    custom emojis declared in the status payload render as inline
    Gtk.Images, links stay underlined and keyboard reachable, URLs keep
    their compact form and paragraph blank-line flow is preserved.
    The raw string (shortcodes intact) remains the accessible label."""

    def __init__(self):
        super().__init__(emoji_size=20, shorten_links=True,
                         paragraph_gap=True)
        self.add_css_class('body')
        self.add_css_class('status-content')


def _display_scale():
    """Largest scale of any monitor: a decoded image must stay sharp on
    whichever one the window ends up on."""
    display = Gdk.Display.get_default()
    monitors = display.get_monitors() if display is not None else None
    scale = 1.0
    for index in range(monitors.get_n_items() if monitors else 0):
        monitor = monitors.get_item(index)
        scale = max(scale, monitor.get_scale())
    return scale


def _peek_bytes(data, count):
    """The first *count* bytes of a ``GLib.Bytes``, without copying the
    rest of a multi-megabyte upload."""
    stream = Gio.MemoryInputStream.new_from_bytes(data)
    try:
        chunk = stream.read_bytes(count, None)
    except GLib.Error:
        return b''
    return chunk.get_data() or b''


def _jpeg_pixel_size(data):
    """Width and height from a JPEG SOF marker, or None when the header
    is not a JPEG we can scan. Dimensions sit a few kilobytes in, past
    the APP segments, so this reads a bounded prefix rather than the
    compressed image."""
    blob = _peek_bytes(data, 65536)
    index = 2
    limit = len(blob)
    while index + 8 < limit:
        if blob[index] != 0xFF:
            return None
        while index < limit and blob[index] == 0xFF:
            index += 1
        if index >= limit:
            return None
        marker = blob[index]
        index += 1
        # Standalone markers (SOI, EOI, restart) carry no length.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            continue
        if index + 2 > limit:
            return None
        segment = int.from_bytes(blob[index:index + 2], 'big')
        if segment < 2:
            return None
        # Start-of-frame markers except DHT (C4) and JPG (C8).
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                      0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            if index + 7 > limit:
                return None
            height = int.from_bytes(blob[index + 3:index + 5], 'big')
            width = int.from_bytes(blob[index + 5:index + 7], 'big')
            if width and height:
                return width, height
            return None
        index += segment
    return None


def _image_pixel_size(data):
    """Width and height stored in the file header. None when the format
    does not put them up front: the caller then decodes through
    GdkPixbuf, which can still scale the image down."""
    head = _peek_bytes(data, 32)
    if head.startswith(b'\x89PNG\r\n\x1a\n') and len(head) >= 24:
        return (int.from_bytes(head[16:20], 'big'),
                int.from_bytes(head[20:24], 'big'))
    if head[:6] in (b'GIF87a', b'GIF89a') and len(head) >= 10:
        return (int.from_bytes(head[6:8], 'little'),
                int.from_bytes(head[8:10], 'little'))
    if head.startswith(b'\xff\xd8'):
        return _jpeg_pixel_size(data)
    return None


def _decode_covering(data, cover, scale):
    """Texture of the image in *data*, decoded no larger than it takes to
    cover a *cover* (width, height) box of logical pixels on a display of
    *scale*, which the caller reads on the main thread (this also runs on
    a worker, see ``_decode_async``). None when the image cannot be read.

    GdkPixbuf's loader plus the copy into a ``Gdk.MemoryTexture`` is
    several times slower than ``Gdk.Texture.new_from_bytes``. When the
    file is already no larger than the tile there is nothing to scale
    away, so that copy buys neither sharpness nor a smaller texture.
    Oversized originals still go through GdkPixbuf with ``set_size``,
    which decodes them already scaled down instead of holding the full
    bitmap."""
    box_width, box_height = cover[0] * scale, cover[1] * scale
    natural = _image_pixel_size(data)
    if natural is not None:
        width, height = natural
        if width > 0 and height > 0 and max(box_width / width,
                                             box_height / height) >= 1:
            try:
                return Gdk.Texture.new_from_bytes(data)
            except GLib.Error:
                pass

    def prepared(loader, width, height):
        factor = max(box_width / width, box_height / height)
        if factor < 1:
            loader.set_size(max(1, round(width * factor)),
                            max(1, round(height * factor)))

    loader = GdkPixbuf.PixbufLoader()
    loader.connect('size-prepared', prepared)
    try:
        loader.write_bytes(data)
        loader.close()
    except GLib.Error:
        return None
    pixbuf = loader.get_pixbuf()
    if pixbuf is None:
        return None
    if pixbuf.get_has_alpha():
        memory_format = Gdk.MemoryFormat.R8G8B8A8
    else:
        memory_format = Gdk.MemoryFormat.R8G8B8
    return Gdk.MemoryTexture.new(pixbuf.get_width(), pixbuf.get_height(),
                                 memory_format, pixbuf.read_pixel_bytes(),
                                 pixbuf.get_rowstride())


def _decode_async(data, cover, cancellable, callback):
    """Decode *data* on a worker thread, no larger than a *cover* box
    needs (at full size when *cover* is None), and call
    ``callback(texture, error)`` on the main context: the texture, or
    None and the reason. Nothing is called once *cancellable* fires.

    A media decode costs milliseconds per image, a stall in the frame
    whenever a page of posts arrives. GdkPixbuf and
    ``Gdk.Texture.new_from_bytes`` are safe off the main thread; the
    display scale is not, so it is read here first."""
    scale = _display_scale() if cover else 1.0

    def work(task, _source, _data, _cancellable):
        # Worker thread: decoding only, no widget or display access.
        texture = error = None
        try:
            if cover:
                texture = _decode_covering(data, cover, scale)
            if texture is None:
                texture = Gdk.Texture.new_from_bytes(data)
        except GLib.Error as failure:
            error = failure.message
        finally:
            # Always return, or the task never calls back.
            task.return_value(GObject.Value(GObject.TYPE_PYOBJECT,
                                            (texture, error)))

    def done(_source, task, _data):
        if cancellable is not None and cancellable.is_cancelled():
            return
        _ok, value = task.propagate_value()
        callback(*value)

    task = Gio.Task.new(None, cancellable, done, None)
    # A cancelled decode reports at once; its result is dropped.
    task.set_return_on_cancel(True)
    task.run_in_thread(work)


class _BrokenImage(GObject.Object, Gdk.Paintable, Gtk.SymbolicPaintable):
    """Stand-in for an image that failed to load: image-missing centred
    at its natural size, whatever the picture's size or content fit. It
    has no intrinsic size, so the picture keeps the box it was given,
    and as a symbolic paintable it takes the picture's colours."""

    ICON_SIZE = 32
    _instance = None

    @classmethod
    def get(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def do_get_flags(self):
        return (Gdk.PaintableFlags.STATIC_SIZE
                | Gdk.PaintableFlags.STATIC_CONTENTS)

    def do_snapshot(self, snapshot, width, height):
        self.do_snapshot_symbolic(snapshot, width, height, [])

    def do_snapshot_symbolic(self, snapshot, width, height, colors, *_n):
        display = Gdk.Display.get_default()
        if display is None:
            return
        icon = Gtk.IconTheme.get_for_display(display).lookup_icon(
            'image-missing-symbolic', None, self.ICON_SIZE, 1,
            Gtk.TextDirection.NONE, 0)
        size = min(self.ICON_SIZE, width, height)
        snapshot.save()
        snapshot.translate(Graphene.Point().init((width - size) / 2,
                                                 (height - size) / 2))
        icon.snapshot_symbolic(snapshot, size, size, colors)
        snapshot.restore()


def log_media_failure(url, reason):
    # Shown with G_MESSAGES_DEBUG=Babble: a host outage or a codec gap
    # otherwise only surfaces as placeholders.
    GLib.log_variant('Babble', GLib.LogLevelFlags.LEVEL_DEBUG,
                     GLib.Variant('a{sv}', {'MESSAGE': GLib.Variant(
                         's', 'Media %s failed: %s' % (url, reason))}))


def _show_broken(picture, url=None, reason=None):
    if reason is not None:
        log_media_failure(url, reason)
    picture.set_paintable(_BrokenImage.get())
    picture.add_css_class('broken-image')


def _media_decode_for_key(data, key, cancellable, done):
    """Decode one attachment's bytes for the box it has to fill.

    The cover rides on the cache key (``(url, cover)``, set in
    ``_TextureCache.load``) rather than being passed separately, so one
    Fetcher -- and so one coalescing table -- serves every tile.
    """
    cover = key[1] if isinstance(key, tuple) else None
    _decode_async(data, cover, cancellable,
                  lambda texture, error: done(texture,
                                              error and error.message))


class _TextureCache:
    """Decoded media textures shared between rows by URL and box."""

    # Bounded: scrolling a media-heavy timeline would otherwise keep
    # every attachment ever decoded alive for the life of the process.
    store = imagecache.Store(MEDIA_TEXTURE_CACHE_SIZE)
    fetcher = None

    @classmethod
    def _fetch(cls):
        """The shared fetcher.

        The cover box is per-tile, and the Fetcher's decode is fixed at
        construction -- so the cover rides on the key instead, and the
        decoder reads it back out. That keeps one Fetcher per store (and
        so one coalescing table) rather than one per tile, which is what
        the shared cache is for.
        """
        if cls.fetcher is None:
            cls.fetcher = imagecache.Fetcher(
                cls.store, _media_decode_for_key,
                on_failure=log_media_failure)
        return cls.fetcher

    @classmethod
    def load(cls, url, picture, cover=None):
        """Show *url* in *picture*. With *cover* (width, height), the
        largest box in logical pixels the picture fills, the image is
        decoded only as large as that box needs: an original attachment
        can be tens of megapixels, its thumbnail a few hundred."""
        key = url if cover is None else (url, cover)
        # A rebuilt row replaces the request it already had in flight;
        # cancelling the old one stops the bytes for a thumbnail nobody
        # will display any more.
        previous = getattr(picture, '_babble_cancel', None)
        if previous is not None:
            previous.cancel()
        cancellable = Gio.Cancellable.new()
        picture._babble_cancel = cancellable

        def arrived(texture, reason):
            if texture is None:
                if not cancellable.is_cancelled():
                    _show_broken(picture, url, reason)
                return
            picture.remove_css_class('broken-image')
            if picture.get_paintable() is None \
                    or picture.get_paintable() is not texture:
                picture.set_paintable(texture)

        cls._fetch().fetch(url, arrived, cancellable=cancellable, key=key)

    @classmethod
    def session(cls):
        return imagecache.session()

