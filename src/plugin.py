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
- :class:`LinkExtension`, once per enabled plugin: it claims the links
  to the origins it names, so they open in the plugin's own way.
- :class:`PreferencesExtension`, each time the plugin's settings open
  from the Plugins page: it fills them, and goes when they close.

Extensions of one plugin share its :class:`Context`, and through
:attr:`Context.data` whatever state they have in common.

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

A plugin can also show a web page in Babble's web column and script it:
:meth:`Context.open_panel` returns a :class:`Panel`. There is one
column, so one panel: opening another (from any plugin, or an ordinary
link opened in the column) closes the previous handle. A closed handle
never calls back, and neither does one whose plugin was disabled; the
user hiding the column, or disabling the plugin, closes the panel.
Scripting is limited to the origin the plugin opened and runs in a world
of its own, which the page cannot see or call (see :class:`Panel`).

A panel also tells the plugin what happens to the page:
:meth:`Panel.on_load` after each full page load that finished,
:meth:`Panel.on_close` when the user or another page took the column. A
script that fails reports a :class:`PanelError`, whose ``code`` is one of
the ``ERROR_*`` constants, stable across releases (the message is not).

Exceptions raised by plugin code are caught: the plugin is disabled and
the user is told, and the app carries on.
"""

import gettext as _gettext
from dataclasses import dataclass, field

API_VERSION = 1

# The API grows inside a version. A plugin that needs more than the first
# revision says so in its ``.plugin`` file, as ``X-Babble-Api-Revision=N``
# (absent means 1), and Babble refuses to load it when it provides less.
#
# Revisions:
#   1  post menu, hashtag banner, preferences (Babble 0.3.0)
#   2  panels, panel events (on_load, on_close), error codes, account
#      info, link claiming
API_REVISION = 2

# The ``code`` of a :class:`PanelError`.
ERROR_WRONG_ORIGIN = 'wrong-origin'  # the page is not on the panel's origin
ERROR_JAVASCRIPT_OFF = 'javascript-off'  # the Run JavaScript preference
ERROR_SCRIPT = 'script'  # anything else: the script threw, or WebKit failed


class PanelError(str):
    """The *error* :meth:`Panel.run_script` reports: a readable message
    (a ``str``, so code that treats it as one keeps working) with a
    stable :attr:`code`, one of ``ERROR_WRONG_ORIGIN``,
    ``ERROR_JAVASCRIPT_OFF`` or ``ERROR_SCRIPT``. Test the code, never
    the message, which is for people and may be reworded."""

    def __new__(cls, message, code=ERROR_SCRIPT):
        error = super().__new__(cls, message)
        error.code = code
        return error


def gettext(message):
    """*message* translated through Babble's own catalogue: for the
    plugins Babble ships, whose strings are in its ``po/``. A plugin
    from elsewhere brings its own catalogue (``gettext.translation``)."""
    return _gettext.dgettext('babble', message)


def ngettext(singular, plural, n):
    """The plural form of :func:`gettext`."""
    return _gettext.dngettext('babble', singular, plural, n)


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
    author_handle: str = ''  # always @user@server, local authors included


@dataclass(frozen=True)
class Account:
    """The signed-in account, as far as a plugin may know it: its
    ``handle`` (``@user@server``), the ``instance`` URL it is on
    (``https://example.org``) and that server's ``host``
    (``example.org``). Never its id or its token."""
    handle: str
    instance: str
    host: str


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

    def request(self, method, url, callback, json=None, form=None,
                headers=None):
        """:meth:`Context.request`, never calling back once this
        extension is gone."""
        return self.context.request(method, url, callback, json=json,
                                    form=form, headers=headers, owner=self)


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


class LinkExtension(Extension):
    """Claims links to the servers a plugin knows, so Babble opens them
    the plugin's way instead of in the web column or the browser.

    One instance per enabled plugin. A link is claimed only when its
    origin is one of :meth:`origins` (never any other, whatever
    :meth:`claims` would say), when the user chose to open links in the
    app (with that preference off every link goes to the browser, as the
    user asked, and nothing is claimed), and when :meth:`claims` agrees.
    The first enabled plugin, in the order they were loaded, that claims
    a link gets it. A claimed link opens through :meth:`open` and
    nothing else: not as a post or profile in Babble, even when its path
    looks like one.

    A plugin can never claim the user's own server: a link whose origin
    is that of the signed-in account's instance is never claimed,
    whatever :meth:`origins` and :meth:`claims` say, and the rule follows
    an account switch. With nobody signed in nothing is protected.

    Babble's explicit "Open in Browser" is never claimed, and a menu
    entry that calls :meth:`Context.open_panel` always works, since the
    user asked for it. A link clicked inside a plugin's panel on a
    claimed origin stays in the panel."""

    def origins(self):
        """The origins this plugin claims, an iterable of
        ``scheme://host[:port]`` strings in lower case, such as
        ``https://example.org``. Read on every click, so a change in the
        plugin's settings takes effect at once; keep it cheap."""
        return ()

    def claims(self, url):
        """Whether to claim *url*, a link on one of :meth:`origins`.
        Called only for such links; the default claims all of them."""
        return True

    def open(self, url, widget):
        """Open *url*, which this plugin claimed. *widget* is something
        on screen, to find the window by or anchor a toast to; the usual
        thing is :meth:`Context.open_panel`. Handing the same *url* back
        to :meth:`Context.open_link` from here opens it the ordinary way
        instead of coming back to this method."""
        raise NotImplementedError


class PreferencesExtension(Extension):
    """The plugin's own settings, opened from Babble's Plugins page.

    One instance each time they open; it is deactivated when they
    close, or when the plugin is disabled. With none, the Plugins page
    shows the plugin without a settings button."""

    def create_preferences(self):
        """A Gtk.Widget for the settings page, typically one or more
        Adw.PreferencesGroup in a Gtk.Box. Babble owns it once returned.
        Called once, after :meth:`activate`."""
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

    @property
    def account(self):
        """The signed-in account, an :class:`Account`, or None when
        nobody is signed in. Read when asked, so it follows an account
        switch."""
        raise NotImplementedError

    @property
    def data(self):
        """A dict for state the plugin's extensions share while it is
        enabled (a session, a cache). Emptied when it is disabled."""
        raise NotImplementedError

    def connect(self, emitter, signal, callback, *extra, owner=None):
        """``emitter.connect(signal, callback, *extra)``, disconnected
        when the plugin is disabled, or when *owner*, an extension, goes.
        Returns the handler id.

        A *callback* that is a method of a GObject (a widget or dialog
        class of the plugin's) is held weakly, so a dialog connecting its
        own buttons to its own methods is freed when it closes. Prefer
        such methods to lambdas: a lambda that captures the widget keeps
        it alive until the owner goes."""
        raise NotImplementedError

    def disconnect(self, emitter, handler_id):
        """Disconnect a handler :meth:`connect` made, before its time."""
        raise NotImplementedError

    def request(self, method, url, callback, json=None, form=None,
                headers=None, owner=None):
        """Send an HTTP request to a server other than Mastodon.

        *url* is ``https://`` or ``http://``. *json*, when given, is sent
        as a JSON body; *form*, a dict, as a form (``application/
        x-www-form-urlencoded``) instead. *callback* is called on the main loop as
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

    def open_panel(self, widget, url, title=None):
        """Show *url*, ``https://`` or ``http://``, in Babble's web column
        and return its :class:`Panel`. The column is shown whatever the
        user chose for opening links in the app; *widget* is something on
        screen, to find the window by. *title* is shown until the page
        has one.

        Without a web engine (or a window with a column) the page opens in
        the browser instead and the Panel returned is already closed."""
        raise NotImplementedError

    def log(self, message):
        """Write *message* to the journal, marked with the plugin's id."""
        raise NotImplementedError


class Panel:
    """A page the plugin showed in the web column, and the handle to
    control it. Babble never gives the page anything of the signed-in
    account, and the plugin gets only the handle and server
    (:attr:`Context.account`); the account's id and token are given to
    neither.

    Scripting is bound to the origin the plugin opened (:attr:`origin`,
    that of the URL given to :meth:`Context.open_panel` or
    :meth:`load_uri`). While the page is on another origin (a link the
    user followed, a sign-in on the user's own server), :meth:`run_script`
    reports an error and messages are dropped, until it is back.

    Script runs in an isolated world, one per plugin: it shares the
    page's DOM, but the page's own script cannot see its variables or
    the message channel, so a web page can never message Babble. Fields
    set from script must be told to the page the way a user would (an
    ``input`` event after setting a value). Scripting follows the *Run
    JavaScript* preference: with it off, :meth:`run_script` reports an
    error.

    :meth:`toast` and :meth:`open_link` let the plugin talk to the user
    and open a link from its panel without being given any widget: Babble
    anchors both on the panel's own column, on purpose, so a plugin never
    holds a piece of Babble's interface.

    A closed handle does nothing and never calls back, whether the user
    hid the column, another page replaced this one, or the plugin was
    disabled."""

    @property
    def uri(self):
        """The address the page is on now, or the last one it was on."""
        raise NotImplementedError

    @property
    def origin(self):
        """The origin scripting is bound to, such as
        ``https://example.org``."""
        raise NotImplementedError

    @property
    def is_open(self):
        """False once the handle is closed."""
        raise NotImplementedError

    def load_uri(self, url):
        """Load *url* in the panel; scripting is bound to its origin
        from then on."""
        raise NotImplementedError

    def close(self):
        """Hide the column, if it still shows this page."""
        raise NotImplementedError

    def toast(self, text):
        """Show *text* (plain text, shown as given) as a toast in the
        panel's window. Does nothing once the handle is closed. No
        widget is exposed, on purpose."""
        raise NotImplementedError

    def open_link(self, url):
        """Open *url* the way a link in a post opens: a thread, profile
        or hashtag inside Babble, anything else in the web column or the
        browser, as the user chose. Anchored on the panel's own column,
        and it goes through the usual routing, so a link another plugin
        claims is that plugin's (never the user's own server, see
        :class:`LinkExtension`). Does nothing once the handle is closed.
        No widget is exposed, on purpose."""
        raise NotImplementedError

    def run_script(self, script, callback=None):
        """Run the JavaScript *script* in the page. *callback*, when
        given, is called on the main loop as ``callback(result, error)``:
        *result* is what the script's last expression gave, as plain JSON
        data (None for nothing), *error* None or a :class:`PanelError`.
        Nothing is called back, not even an error, once the handle is
        closed."""
        raise NotImplementedError

    def on_message(self, callback):
        """Receive what script this plugin ran posts, with
        ``window.webkit.messageHandlers.babble.postMessage(data)``:
        ``callback(data)``, *data* plain JSON data. Replaces the previous
        callback; None stops it."""
        raise NotImplementedError

    def on_load(self, callback):
        """``callback(uri)`` each time a full page load finishes, the
        first one and every navigation after it, redirects included;
        *uri* is where the page is, so check :attr:`origin` before
        scripting it. Not called for a load that failed (the column shows
        an error), nor after the handle closed. It does not see
        ``history.pushState`` or a fragment change: a plugin for a
        single-page app watches the DOM from :meth:`run_script`. Replaces
        the previous callback; None stops it."""
        raise NotImplementedError

    def on_close(self, callback):
        """``callback(reason)`` when Babble ends this panel: *reason* is
        ``'hidden'`` (the user hid the column, or the window closed) or
        ``'replaced'`` (another panel, or a link opened in the column,
        took it). Called once, never from inside the call that ended the
        panel (so it may open another panel), and only while the plugin
        is enabled. Not called for the plugin's own :meth:`close`, nor
        when the plugin is disabled. Replaces the previous callback; None
        stops it."""
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
