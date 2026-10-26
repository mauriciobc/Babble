# SPDX-License-Identifier: GPL-3.0-or-later

"""The API a Babble plugin is written against, imported as
``from babble import plugin``.

This module is the whole contract. A plugin imports nothing else of
Babble's; the `plugin boundary` test fails the build when one does,
because every other module is free to change between releases and this
one is the only one that is versioned.

API_VERSION 1 is a preview: it may still change. A plugin states the
version it was written for in its ``.plugin`` file, as
``X-Babble-Api=1``, and Babble refuses to load one that names another.

A plugin module defines one or more subclasses of the extension classes
below; Babble finds them when the plugin is enabled. Each is created
with the plugin's :class:`Context` and lives exactly as long as the
thing it extends:

- :class:`PostMenuExtension`, once per enabled plugin: it is asked for
  menu items each time a post's menu opens.
- :class:`HashtagPageExtension`, once per open hashtag page: it may give
  the page a banner, which goes when the page does.

Signal connections and HTTP requests are owned by Babble and released
with what they belong to. Made with an extension's own
:meth:`Extension.connect` and :meth:`Extension.request`, they go when
that extension does (for a banner, when its page closes); made with the
Context's, when the plugin is disabled. A released handler is
disconnected, and a request still in flight never calls back.

Connect signals through them, not with ``widget.connect``: a handler
that reaches the extension from a widget the extension holds is a
reference cycle through C that Python's collector cannot see, and the
page it is on would never be freed. The `plugin boundary` test fails
on a bare ``.connect(`` in the plugins Babble ships.

Exceptions raised by plugin code are caught: the plugin is disabled and
the user is told, and the app carries on.
"""

from dataclasses import dataclass, field

API_VERSION = 1


@dataclass(frozen=True)
class Media:
    """One attachment of a post."""
    kind: str  # 'image', 'video', 'gifv', 'audio' or 'unknown'
    url: str
    description: str = ''


@dataclass(frozen=True)
class Post:
    """A read-only copy of a post, as the menu it was opened from shows
    it: for a boost, the boosted post.

    ``id`` is local to ``instance``, the server the signed-in account is
    on; ``url`` and ``uri`` are the same everywhere."""
    id: str
    url: str
    uri: str
    instance: str
    author: str  # acct: user, or user@server for a remote account
    author_name: str
    text: str  # the content as plain text
    tags: tuple = ()
    language: str = ''
    created_at: str = ''  # ISO 8601, as the server sent it
    visibility: str = ''
    sensitive: bool = False
    spoiler_text: str = ''
    media: tuple = field(default=())


@dataclass(frozen=True)
class MenuItem:
    """An entry a plugin adds to a post's menu.

    *label* is shown as given (use an underscore for the access key);
    *activate* is called as ``activate(post, widget)``, where *widget* is
    something on screen to anchor a toast or a dialog to."""
    label: str
    activate: object


class Extension:
    """Base of every extension class. ``self.context`` is the plugin's
    :class:`Context`, shared by all its extensions."""

    def __init__(self, context):
        self.context = context

    def activate(self):
        """Called once the extension exists, before it is used."""

    def deactivate(self):
        """Called before Babble drops the extension: the plugin was
        disabled, the app is quitting, or the thing it extends is gone.
        Babble releases what went through :meth:`connect` and
        :meth:`request` on its own; this is for anything else."""

    def connect(self, emitter, signal, callback, *extra):
        """:meth:`Context.connect`, released with this extension."""
        return self.context.connect(emitter, signal, callback, *extra,
                                    owner=self)

    def request(self, method, url, callback, json=None, headers=None):
        """:meth:`Context.request`, never calling back once this
        extension is gone."""
        return self.context.request(method, url, callback, json=json,
                                    headers=headers, owner=self)


class PostMenuExtension(Extension):
    """Adds entries to the menu of a post.

    One instance per enabled plugin. :meth:`menu_items` runs as the menu
    opens, never while the timeline scrolls; keep it quick and do not
    start network requests there."""

    def menu_items(self, post):
        """The entries for *post*, a :class:`Post`: a list of
        :class:`MenuItem`, empty when the plugin has nothing for it."""
        return []


class HashtagPageExtension(Extension):
    """Adds a banner above the posts of a single-hashtag page.

    One instance per open page; it is deactivated when the page goes.
    ``self.hashtag`` is the tag, lower-case and without the ``#``."""

    def __init__(self, context, hashtag):
        super().__init__(context)
        self.hashtag = hashtag

    def create_banner(self):
        """A Gtk.Widget to show above the posts, or None for no banner.

        Babble owns the widget once returned: it is removed when the page
        goes or the plugin is disabled. Called once, after
        :meth:`activate`."""
        return None


class Context:
    """What a plugin can reach of Babble. One per enabled plugin; the
    implementation is Babble's, the methods are the API.

    ``plugin_id`` is the plugin's module name; ``version`` the version
    from its ``.plugin`` file."""

    plugin_id = ''
    version = ''

    @property
    def settings(self):
        """The plugin's own settings, a :class:`Settings`."""
        raise NotImplementedError

    def connect(self, emitter, signal, callback, *extra, owner=None):
        """``emitter.connect(signal, callback, *extra)``, disconnected
        when the plugin is disabled, or when *owner*, an extension, goes.
        Returns the handler id."""
        raise NotImplementedError

    def disconnect(self, emitter, handler_id):
        """Disconnect a handler :meth:`connect` made, before its time."""
        raise NotImplementedError

    def request(self, method, url, callback, json=None, headers=None,
                owner=None):
        """Send an HTTP request to a server other than Mastodon.

        *url* is ``https://`` or ``http://``. *json*, when given, is sent
        as a JSON body. *callback* is called on the main loop as
        ``callback(response)``, a :class:`Response`, unless the plugin was
        disabled, or *owner* went, in the meantime: then it is not
        called."""
        raise NotImplementedError

    def store_secret(self, name, value, callback=None):
        """Keep *value*, a string, in the keyring under *name*.
        *callback*, when given, receives None or an error message."""
        raise NotImplementedError

    def lookup_secret(self, name, callback):
        """Read the secret stored under *name*: ``callback(value, error)``,
        *value* None when there is none or it could not be read."""
        raise NotImplementedError

    def clear_secret(self, name, callback=None):
        """Remove the secret stored under *name*."""
        raise NotImplementedError

    def toast(self, widget, text):
        """Show *text* in the toast overlay *widget* is in."""
        raise NotImplementedError

    def open_link(self, widget, url):
        """Open *url* the way a link in a post opens: a post, profile or
        hashtag inside Babble, anything else in the browser."""
        raise NotImplementedError

    def log(self, message):
        """Write *message* to the journal, marked with the plugin's id."""
        raise NotImplementedError


class Settings:
    """A plugin's settings: string keys, values JSON can hold. Saved as
    each value is set."""

    def get(self, key, default=None):
        raise NotImplementedError

    def set(self, key, value):
        raise NotImplementedError

    def remove(self, key):
        raise NotImplementedError


@dataclass(frozen=True)
class Response:
    """The outcome of :meth:`Context.request`.

    ``status`` is the HTTP status, 0 when no response arrived; ``error``
    a message when the request failed (no connection, a status of 400 or
    more, a body that is not the JSON it claimed to be), else None.
    ``data`` is the parsed JSON body, or None; ``text`` the body as
    text."""
    status: int
    data: object = None
    text: str = ''
    error: object = None

    @property
    def ok(self):
        return self.error is None
