# SPDX-License-Identifier: GPL-3.0-or-later

"""Answer the questions people ask you on Na Real, and share your link.

Na Real (https://codeberg.org/thiagojedi/na-real) is an anonymous Q&A
app for the fediverse: people ask an account a question without saying
who they are, and the account answers. It is a web app that needs
JavaScript, so the plugin shows it in Babble's web column and scripts
the page a little. It adds two entries to the main menu, "My Questions"
(the dashboard) and "Share My Questions Link" (the composer, with an
invitation and your public link), and nothing to anyone's post or
profile. The page scripts:

- on the login page the signed-in account's server is typed into the
  form, the way the user would, and pressing Log in is left to them;
- on the pages that list questions (the dashboard and the archive) a
  click on "Ver postagem", the link to an answer, opens the thread in
  Babble's timeline instead of a new browser window;
- on the pages with Na Real's navigation bar, its share button opens
  Babble's composer with the user's question title and link, instead of
  copying the link to the clipboard;
- on the page of Na Real's own hashtag, a row that opens it.

Each Na Real deployment is its own server, so which one to use is a
setting, and links to that server open in the panel too. The server can
never be the account's own: that would take over every link to it.

Nothing here uses a timer or keeps a widget; the one open panel is held
as its handle in ``Context.data``.
"""

import json
import unicodedata
from urllib.parse import quote, urlsplit

from gi.repository import Adw, Gtk, Pango

from babble import plugin

_ = plugin.gettext

DEFAULT_SERVER = 'https://na-real.bolha.dev'
DEFAULT_HASHTAG = 'nareal'
LOCAL_HOSTS = ('localhost', '127.0.0.1', '::1')
TITLE = 'Na Real'
MAX_ANSWER_LENGTH = 2048
MAX_SHARE_URL_LENGTH = 2048
MAX_SHARE_TEXT_LENGTH = 500

# What the page scripts look for, in one place. Na Real's labels are in
# Portuguese and are never matched.
SELECTORS = {
    'instance': '#instance',  # the login form's one field
    'answer_link': '.fixed-grid .cell a[target="_blank"]',  # "Ver postagem"
    # The navbar's share button, "Compartilhar": found by its place in the
    # bar, never by its label.
    'share_button': 'nav.navbar .navbar-end button.button',
}

# Pages that list questions. A restored question keeps its answer, so an
# answered card can be on the dashboard as well as in the archive.
QUESTION_PAGES = ('/dashboard', '/archived')

# Pages that carry the navbar's share button (the user's own, signed in).
SHARE_PAGES = ('/dashboard', '/archived', '/settings')


def _script(template, **values):
    """*template* with each ``__NAME__`` replaced by *values*'s NAME as
    a JavaScript literal."""
    for name, value in values.items():
        template = template.replace('__%s__' % name.upper(),
                                    json.dumps(value))
    return template


# Fills the login form's instance field, once. Never submits.
LOGIN_SCRIPT = '''(function () {
  try {
    var field = document.querySelector(__INSTANCE__);
    if (!field || field.value) { return 'skipped'; }
    field.value = __HOST__;
    field.dispatchEvent(new Event('input', {bubbles: true}));
    return 'filled';
  } catch (e) { return 'error'; }
})()'''

# One delegated capture-phase listener, since the list re-renders as
# questions are archived or restored. A link to another origin is sent
# to Babble instead of the page.
ANSWER_SCRIPT = '''(function () {
  try {
    if (window.__narealAnswers) { return 'installed'; }
    window.__narealAnswers = true;
    document.addEventListener('click', function (e) {
      try {
        var a = e.target && e.target.closest
          ? e.target.closest(__LINK__) : null;
        if (!a) { return; }
        var u = new URL(a.href);
        if (u.origin === location.origin) { return; }
        if (u.protocol !== 'http:' && u.protocol !== 'https:') { return; }
        e.preventDefault();
        e.stopPropagation();
        window.webkit.messageHandlers.babble.postMessage({answer: u.href});
      } catch (err) {}
    }, true);
    return 'installed';
  } catch (e) { return 'error'; }
})()'''

# Takes over the navbar's share button, which would copy the link to the
# clipboard: the click is stopped before Na Real's handler hears it, and
# the user's question title and link, read from the settings form (this
# page's or the fetched one), go to Babble. An unreadable form is posted
# as an empty share so the host can fall back.
SHARE_SCRIPT = '''(function () {
  try {
    if (window.__narealShare) { return 'installed'; }
    window.__narealShare = true;
    function send(share) {
      try {
        window.webkit.messageHandlers.babble.postMessage({share: share});
      } catch (err) {}
    }
    function read(doc) {
      try {
        var url = doc.querySelector('input[name="url"]');
        var title = doc.querySelector('[name="question_title"]');
        if (!url || !url.value) { return {}; }
        return {text: title ? title.value : '',
                url: location.origin + '/' + url.value};
      } catch (err) { return {}; }
    }
    document.addEventListener('click', function (e) {
      try {
        var b = e.target && e.target.closest
          ? e.target.closest(__BUTTON__) : null;
        if (!b) { return; }
        e.preventDefault();
        e.stopPropagation();
        if (location.pathname === '/settings') {
          send(read(document));
          return;
        }
        fetch('/settings', {credentials: 'same-origin'})
          .then(function (r) { return r.ok ? r.text() : ''; })
          .then(function (html) {
            send(html ? read(new DOMParser().parseFromString(
              html, 'text/html')) : {});
          })
          .catch(function () { send({}); });
      } catch (err) { send({}); }
    }, true);
    return 'installed';
  } catch (e) { return 'error'; }
})()'''


def normalize_server(text):
    """The server's address without a trailing slash, or None. Plain
    http only on this machine."""
    text = (text or '').strip().rstrip('/')
    if not text:
        return None
    if '://' not in text:
        text = 'https://' + text
    parts = urlsplit(text)
    if not parts.hostname or parts.scheme not in ('https', 'http'):
        return None
    if parts.scheme == 'http' and parts.hostname not in LOCAL_HOSTS:
        return None
    return text


def origin_of(url):
    """``scheme://host[:port]`` of an http(s) *url*, lower case, else ''."""
    try:
        parts = urlsplit(url or '')
        host, port = parts.hostname, parts.port
    except ValueError:
        return ''
    if parts.scheme.lower() not in ('http', 'https') or not host:
        return ''
    if ':' in host:
        host = '[%s]' % host
    origin = '%s://%s' % (parts.scheme.lower(), host)
    return origin if port is None else '%s:%d' % (origin, port)


def server_of(context):
    return normalize_server(context.settings.get('server')) \
        or DEFAULT_SERVER


def is_own_server(context, server):
    """Whether *server* is the signed-in account's own instance."""
    account = context.account
    return account is not None and bool(origin_of(server)) \
        and origin_of(server) == origin_of(account.instance)


def own_url(context):
    """The signed-in account's public Na Real page, or None with nobody
    signed in. Na Real's lookup is exact: ``<server>/@user@host`` with
    the host in lower case and a plain ``@``, nothing else is found."""
    account = context.account
    if account is None or not account.handle:
        return None
    name, at, host = account.handle.lstrip('@').rpartition('@')
    handle = '@%s@%s' % (name, host.lower()) if at else '@' + host.lower()
    return '%s/%s' % (server_of(context), quote(handle, safe='@'))


def invitation(context, url):
    """The text that invites people to ask *url*'s owner something."""
    # Translators: the first line of a post that invites people to ask
    # the author questions; a link and a hashtag follow it. Na Real is
    # the name of a service.
    ask = _('Ask me anything, anonymously, on Na Real:')
    tag = tag_of(context.settings.get('hashtag')) or DEFAULT_HASHTAG
    return '%s\n%s\n\n#%s' % (ask, url, tag)


def tag_of(text):
    """*text* as a hashtag name (lower case, no ``#``), or None when it
    is not one word."""
    tag = (text or '').strip().lstrip('#').lower()
    if not tag or not all(c.isalnum() or c == '_' for c in tag):
        return None
    return tag


def answer_url(data, origin):
    """The thread link in a page's message, or None: only an http(s)
    URL of reasonable size that is not on Na Real itself."""
    if not isinstance(data, dict):
        return None
    url = data.get('answer')
    if not isinstance(url, str) or len(url) > MAX_ANSWER_LENGTH \
            or any(c.isspace() for c in url):
        return None
    found = origin_of(url)
    if not found or found == origin:
        return None
    return url


def share_of(data, origin):
    """What a page's share message holds. None: not a share message, or
    one to drop. ``{}``: the page could not read the user's link, so the
    host builds it. Otherwise ``(text, url)``: an http(s) URL of the
    server itself and a plain, short text."""
    if not isinstance(data, dict) or not isinstance(data.get('share'), dict):
        return None
    share = data['share']
    if not share:
        return {}
    url, text = share.get('url'), share.get('text', '')
    if not isinstance(url, str) or not isinstance(text, str) \
            or len(url) > MAX_SHARE_URL_LENGTH \
            or len(text) > MAX_SHARE_TEXT_LENGTH \
            or any(c.isspace() for c in url) \
            or any(unicodedata.category(c) == 'Cc' for c in text) \
            or origin_of(url) != origin:
        return None
    return text, url


def scripts_for(context, path):
    """The scripts to run on a page at *path*."""
    scripts = []
    if path == '/login':
        account = context.account
        if account is not None and account.host:
            scripts.append(_script(LOGIN_SCRIPT, instance=SELECTORS['instance'],
                                   host=account.host))
    if path in QUESTION_PAGES:
        scripts.append(_script(ANSWER_SCRIPT, link=SELECTORS['answer_link']))
    if path in SHARE_PAGES:
        scripts.append(_script(SHARE_SCRIPT, button=SELECTORS['share_button']))
    return scripts


def attach(context, panel):
    """Script *panel*, a Na Real page this plugin opened, and keep its
    handle as the current one. Every way of opening a panel goes through
    here."""
    state = {'warned': False}
    if panel.is_open:
        context.data['panel'] = panel

    def on_result(_result, error):
        # The wrong origin is the OAuth detour; anything else is the
        # page's own business. Only the preference needs telling.
        if error is None or getattr(error, 'code', None) \
                != plugin.ERROR_JAVASCRIPT_OFF or state['warned']:
            return
        state['warned'] = True
        panel.toast(_('Na Real needs JavaScript. Turn it on in '
                      'Preferences.'))

    def on_load(uri):
        if origin_of(uri) != panel.origin:
            return
        for script in scripts_for(context, urlsplit(uri).path):
            panel.run_script(script, on_result)

    def on_message(data):
        url = answer_url(data, panel.origin)
        if url is not None:
            panel.open_link(url)
            return
        share = share_of(data, panel.origin)
        if share is None:
            return
        if share:
            text, link = share
            panel.compose(text + '\n' + link if text else link)
            return
        own = own_url(context)  # the page could not be read
        if own is None:
            # Translators: shown when the Na Real page does not tell
            # Babble the user's link. Na Real is the name of a service.
            panel.toast(_('Couldn’t read your Na Real link.'))
        else:
            panel.compose(invitation(context, own))

    def on_close(_reason):
        if context.data.get('panel') is panel:
            del context.data['panel']

    panel.on_load(on_load)
    panel.on_message(on_message)
    panel.on_close(on_close)


def open_page(context, widget, url):
    """Show *url* in the panel and script it."""
    panel = context.open_panel(widget, url, title=TITLE)
    attach(context, panel)
    return panel


class NaRealMainMenu(plugin.MainMenuExtension):
    """Two entries on the main menu, about the user's own questions."""

    def menu_items(self):
        return [
            # Translators: a main menu entry that opens the user's
            # questions on Na Real; the underscore marks the access key.
            # Na Real is the name of a service.
            plugin.MenuItem(_('My _Questions'), self.open_questions),
            # Translators: a main menu entry that starts a post with the
            # user's Na Real link; the underscore marks the access key.
            # Na Real is the name of a service.
            plugin.MenuItem(_('_Share My Questions Link'), self.share_link)]

    def open_questions(self, subject, widget):
        open_page(self.context, widget, server_of(self.context)
                  + '/dashboard')

    def share_link(self, subject, widget):
        url = own_url(self.context)
        if url is None:
            self.context.toast(widget, _('Sign in to Babble first.'))
            return
        anchor = widget.weak_ref()  # the answer may come after it is gone

        def probed(response):
            target = anchor()
            if target is None:
                return
            if response.status == 404:
                # Na Real has never seen this user: signing in from the
                # dashboard is what creates the page.
                self.context.toast(target, _('You are not on Na Real yet. '
                                             'Sign in from My Questions '
                                             'first.'))
                open_page(self.context, target,
                          server_of(self.context) + '/dashboard')
                return
            # Anything else, an error or no answer at all, still offers
            # the invitation: the link is right whatever the probe said.
            self.context.compose(target, invitation(self.context, url))
        self.request('GET', url, probed, headers={'Accept': 'text/html'})


# The banner: everything from here to NaRealLinks is the hashtag page
# extension, which lands as its own commit.

class NaRealBanner(plugin.HashtagPageExtension):
    """A row on the page of Na Real's own hashtag; nothing on any other.
    It asks the network nothing."""

    def create_banner(self):
        configured = tag_of(self.context.settings.get('hashtag')) \
            or DEFAULT_HASHTAG
        if self.hashtag != configured:
            return None
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.list.add_css_class('boxed-list')
        self.row = Adw.ActionRow(
            title='#' + self.hashtag,
            subtitle=_('Anonymous questions for the fediverse'))
        self.button = Gtk.Button(
            icon_name='adw-external-link-symbolic',
            tooltip_text=_('Open Na Real'), valign=Gtk.Align.CENTER)
        self.button.add_css_class('flat')
        self.row.add_suffix(self.button)
        self.list.append(self.row)
        self.connect(self.button, 'clicked', self.on_open)
        return self.list

    def on_open(self, button):
        open_page(self.context, button,
                  server_of(self.context) + '/dashboard')


class NaRealLinks(plugin.LinkExtension):
    """Links to the Na Real server open in the panel."""

    def origins(self):
        server = server_of(self.context)
        if is_own_server(self.context, server):
            return ()
        return (origin_of(server),)

    def open(self, url, widget):
        open_page(self.context, widget, url)


class NaRealPreferences(plugin.PreferencesExtension):
    """Which Na Real to use, and the hashtag of the invitation."""

    def create_preferences(self):
        group = Adw.PreferencesGroup(
            description=_('Na Real is run by each community on its own '
                          'server. Enter the address of the one you use.'))
        self.server = Adw.EntryRow(
            title=_('Server'), show_apply_button=True,
            text=self.context.settings.get('server') or DEFAULT_SERVER)
        self.error = Gtk.Label(wrap=True, xalign=0.0, visible=False,
                               ellipsize=Pango.EllipsizeMode.NONE)
        self.error.add_css_class('error')
        group.add(self.server)
        extras = Adw.PreferencesGroup(
            description=_('Added to the invitation you share from the main '
                          'menu, and the hashtag that shows the Na Real '
                          'banner.'))
        self.hashtag = Adw.EntryRow(
            title=_('Hashtag'), show_apply_button=True,
            text=tag_of(self.context.settings.get('hashtag'))
            or DEFAULT_HASHTAG)
        extras.add(self.hashtag)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.append(group)
        box.append(self.error)
        box.append(extras)
        self.connect(self.server, 'apply', self.on_apply)
        self.connect(self.hashtag, 'apply', self.on_hashtag)
        return box

    def on_apply(self, row):
        text = row.get_text()
        server = normalize_server(text)
        if not (text or '').strip():
            server = DEFAULT_SERVER  # empty goes back to the default
        if server is None:
            self.error.set_label(_('Use the server’s https:// address'))
            self.error.set_visible(True)
            return
        if is_own_server(self.context, server):
            self.error.set_label(_('That is your own server. Use the '
                                   'address of a Na Real server.'))
            self.error.set_visible(True)
            return
        self.error.set_visible(False)
        settings = self.context.settings
        if server == DEFAULT_SERVER:
            settings.remove('server')
        else:
            settings.set('server', server)
        row.set_text(server)
        self.context.toast(row, _('Na Real server saved'))

    def on_hashtag(self, row):
        text = row.get_text()
        tag = tag_of(text) if (text or '').strip() else DEFAULT_HASHTAG
        if tag is None:
            self.error.set_label(_('Use a single hashtag, without spaces'))
            self.error.set_visible(True)
            return
        self.error.set_visible(False)
        settings = self.context.settings
        if tag == DEFAULT_HASHTAG:
            settings.remove('hashtag')
        else:
            settings.set('hashtag', tag)
        row.set_text(tag)
        self.context.toast(row, _('Na Real hashtag saved'))
