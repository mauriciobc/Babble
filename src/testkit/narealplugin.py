#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Na Real plugin against a fake Na Real on loopback, through libpeas
and a real web column.

The fixture serves Na Real's two public pages as the live site sent them
(the login page and an ask page, snapshotted 2026-11-03, MIT licensed),
with a few lines appended that record what reached the page. The pages
behind a login (the dashboard and the archive) are built from
src/islands/QuestionsGrid.tsx and src/common/components/Card.tsx at na-real
commit 5073ff6; the real pages are behind a login.

The synthesized pages (the dashboard, the archive and the settings)
carry the navbar of src/islands/NavBar.tsx and ShareButton.tsx, with a
share button whose own handler counts how often it ran.

Covers the server setting, the two main menu entries (and the absence of
any post menu entry), the user's own link and the probe of their own
page, the login form filled but never submitted, links the plugin
claims, the answer link of a question card, the share button taken over
for Babble's composer, the messages the host refuses, and what is left
behind when a panel closes or the plugin is disabled.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import gc
import http.server
import os
import socket
import sys
import tempfile
import threading
import time

scratch = tempfile.mkdtemp(prefix='babble-nareal-')
os.environ['XDG_CONFIG_HOME'] = os.path.join(scratch, 'config')
os.environ['XDG_DATA_HOME'] = os.path.join(scratch, 'data')
os.environ.pop('BABBLE_PLUGIN_PATH', None)
os.environ.setdefault('WEBKIT_DISABLE_SANDBOX_THIS_IS_DANGEROUS', '1')

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(
    os.path.dirname(os.path.abspath(srcdir)), 'build')
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)
os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

import gi  # noqa: E402

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gio, Gtk  # noqa: E402

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)
try:
    gi.require_version('Peas', '2')
    from gi.repository import Peas  # noqa: E402, F401
except (ImportError, ValueError):
    print('SKIP - libpeas 2 is not installed')
    sys.exit(77)

resource_path = os.path.join(builddir, 'src', 'babble.gresource')
if os.path.exists(resource_path):
    Gio.Resource.load(resource_path)._register()

from src import (accounts, appsettings, feedback, linkrouting,  # noqa: E402
                 pluginhost, pluginmenu)
from src import textemoji, webcolumn  # noqa: E402

if not webcolumn.available():
    print('SKIP - WebKitGTK 6.0 is not available')
    sys.exit(77)

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


def spin(seconds=0.0, until=None):
    context = GLib.MainContext.default()
    deadline = time.monotonic() + seconds
    while True:
        while context.pending():
            context.iteration(False)
        if until is not None and until():
            return True
        if time.monotonic() >= deadline:
            return until is None
        context.iteration(False)
        time.sleep(0.01)


def freed(ref, attempts=5):
    for _attempt in range(attempts):
        gc.collect()
        gc.collect()
        spin(0.05)
        if ref() is None:
            return True
    return ref() is None


# The fake Na Real.

LOGIN_HTML = (
    '<!DOCTYPE html>'
    '<html>'
    '<head>'
    '<meta charset="utf-8"/>'
    '<meta name="viewport" content="width=device-width, initial-scale'
    '=1.0"/>'
    '<title>Na Real (beta)</title>'
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@'
    '1.0.4/css/bulma.min.css"/>'
    '<link rel="stylesheet" href="/styles.css"/>'
    '</head>'
    '<body>'
    '<!--frsh:island:ThemeManager:0:-->'
    '<!--/frsh:island-->'
    '<main>'
    '<div class="login-page">'
    '<div class="login-content">'
    '<h1 class="login-logo">'
    '<img src="/logo.svg?__frsh_c=53b18df75c52f1ddf608f7ca7dbbfc592cc'
    '041ed++-" alt="Na Real Logo" class="login-icon"/>Na Real</h1>'
    '<p class="login-subtitle">Conecte-se para receber <br> perguntas'
    ' anônimas.</p>'
    '<form method="POST">'
    '<div class="login-form-group">'
    '<label class="login-label" for="instance">Informe a sua instânci'
    'a:</label>'
    '<input id="instance" class="login-input" name="instance" placeho'
    'lder="@usuario@dominio" required autocapitalize="none" autocompl'
    'ete="off">'
    '<p class="login-help">O “Na Real” é compatível com Mastodon (inc'
    'luindo Hometown), Pixelfed, Sharkey, GoToSocial e Friendica.</p>'
    '</div>'
    '<button class="button is-primary is-medium is-fullwidth is-round'
    'ed login-button" type="submit">'
    '<span>Entrar</span>'
    '</button>'
    '</form>'
    '</div>'
    '</div>'
    '</main>'
    '<footer class="footer py-4">'
    '<div class="content has-text-centered">'
    '<div class="columns is-mobile">'
    '<div class="column is-half">Made with 🥥🥤 in Brazil by <a href="h'
    'ttps://bolha.us/@jedi" target="_blank" rel="noopener noreferrer"'
    '>Thiago Jedi</a>'
    '</div>'
    '<div class="column is-half">Projeto de <a href="https://codeberg'
    '.org/thiagojedi/na-real" target="_blank" rel="noopener noreferre'
    'r">código aberto</a> sob licença <a href="https://opensource.org'
    '/license/mit" target="_blank" rel="noopener noreferrer">MIT</a>'
    '</div>'
    '</div>'
    '</div>'
    '</footer>'
    '<script type="module" nonce="5902294eeaca439785ed8aa74c0233f9">i'
    'mport { boot } from "/assets/client-entry-C7JapbPI.js";import Th'
    'emeManager from "/assets/fresh-island__ThemeManager-BI4aRFKt.js"'
    ';boot({ThemeManager},"[[1],{\\"slots\\":2,\\"props\\":3},[],{}]");</'
    'script>'
    '</body>'
    '</html>'
).encode()

ASK_HTML = (
    '<!DOCTYPE html>'
    '<html>'
    '<head>'
    '<meta charset="utf-8"/>'
    '<meta name="viewport" content="width=device-width, initial-scale'
    '=1.0"/>'
    '<title>Na Real (beta)</title>'
    '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bulma@'
    '1.0.4/css/bulma.min.css"/>'
    '<link rel="stylesheet" href="/styles.css"/>'
    '</head>'
    '<body>'
    '<!--frsh:island:ThemeManager:2:-->'
    '<!--/frsh:island-->'
    '<main>'
    '<!--frsh:island:NavBar:0:-->'
    '<nav class="navbar" role="navigation" aria-label="main navigatio'
    'n">'
    '<div class="navbar-brand">'
    '<a class="navbar-item" href="/" data-ancestor="true" aria-curren'
    't="true">'
    '<figure class="image">'
    '<img src="/logo.svg?__frsh_c=53b18df75c52f1ddf608f7ca7dbbfc592cc'
    '041ed++-" alt="Na Real logo" class="is-centered"/>'
    '</figure>'
    '<b>Na Real (beta)</b>'
    '</a>'
    '</div>'
    '<input type="checkbox" id="menu" class="is-hidden">'
    '<div id="navbarMenu" class="navbar-menu">'
    '<div class="navbar-start">'
    '</div>'
    '<div class="navbar-end">'
    '</div>'
    '</div>'
    '</nav>'
    '<!--/frsh:island-->'
    '<div class="is-flex is-flex-direction-column is-justify-content-'
    'center is-flex-grow-1">'
    '<noscript>'
    '<div class="modal is-active">'
    '<div class="modal-background">'
    '</div>'
    '<div class="modal-content">'
    '<div class="message is-warning">'
    '<div class="message-header">'
    '<p>ATENÇÃO</p>'
    '</div>'
    '<div class="message-body">'
    '<p>Para enviar perguntas você precisa ativar a execução de JavaS'
    'cript™ nessa página pelo seu navegador.</p>'
    '<p>Estamos trabalhando para diminuir a dependência a essa tecnol'
    'ogia, mas por enquanto é o que tem para hoje.</p>'
    '</div>'
    '</div>'
    '</div>'
    '</div>'
    '</noscript>'
    '<!--frsh:island:QuestionForm:1:-->'
    '<form class="question-box" >'
    '<div class="question-card">'
    '<div class="user-profile">'
    '<img class="user-profile-avatar" src="https://i.cdn.ursal.zone/a'
    'ccounts/avatars/106/461/974/410/609/533/original/e98b256088f6960'
    '6.png" alt="Mauricio Castro :escudoGalo:"/>'
    '<div class="user-profile-info">'
    '<div class="user-profile-name">Mauricio Castro :escudoGalo:</div'
    '>'
    '<a class="user-profile-handle" href="https://ursal.zone/@maurici'
    'obc" rel="me noopener noreferrer" target="_blank" title="@mauric'
    'iobc@ursal.zone">@mauriciobc@ursal.zone</a>'
    '</div>'
    '</div>'
    '<label class="question-card__title" for="question-input">Manda a'
    ' braba</label>'
    '<div  class="grow-wrap question-card__input">'
    '<textarea id="question-input" name="question" wrap="soft"  place'
    'holder="Digite sua pergunta aqui..."  >'
    '</textarea>'
    '</div>'
    '</div>'
    '<button class="button is-primary undefined" type="submit">Pergun'
    'te</button>'
    '</form>'
    '<!--/frsh:island-->'
    '</div>'
    '</main>'
    '<footer class="footer py-4">'
    '<div class="content has-text-centered">'
    '<div class="columns is-mobile">'
    '<div class="column is-half">Made with 🥥🥤 in Brazil by <a href="h'
    'ttps://bolha.us/@jedi" target="_blank" rel="noopener noreferrer"'
    '>Thiago Jedi</a>'
    '</div>'
    '<div class="column is-half">Projeto de <a href="https://codeberg'
    '.org/thiagojedi/na-real" target="_blank" rel="noopener noreferre'
    'r">código aberto</a> sob licença <a href="https://opensource.org'
    '/license/mit" target="_blank" rel="noopener noreferrer">MIT</a>'
    '</div>'
    '</div>'
    '</div>'
    '</footer>'
    '<script type="module" nonce="3436168c774945efa9cdcf97611a396e">i'
    'mport { boot } from "/assets/client-entry-C7JapbPI.js";import { '
    'NavBar } from "/assets/fresh-island__NavBar-CSyjCci0.js";import '
    'QuestionForm from "/assets/fresh-island__QuestionForm-w4bdXp_f.j'
    's";import ThemeManager from "/assets/fresh-island__ThemeManager-'
    'BI4aRFKt.js";boot({NavBar,QuestionForm,ThemeManager},"[[1,4,14],'
    '{\\"slots\\":2,\\"props\\":3},[],{},{\\"slots\\":5,\\"props\\":6},[],{\\"'
    'title\\":7,\\"user\\":8},\\"Manda a braba\\",{\\"_id\\":9,\\"handle\\":10'
    ',\\"display_name\\":11,\\"instance\\":12,\\"avatar\\":13,\\"avatar_stat'
    'ic\\":13},\\"c3d93d10-030f-4998-879a-e3b0af8442c6\\",\\"@mauriciobc@'
    'ursal.zone\\",\\"Mauricio Castro :escudoGalo:\\",\\"ursal.zone\\",\\"h'
    'ttps://i.cdn.ursal.zone/accounts/avatars/106/461/974/410/609/533'
    '/original/e98b256088f69606.png\\",{\\"slots\\":15,\\"props\\":16},[],'
    '{}]");</script>'
    '</body>'
    '</html>'
).encode()

# Appended to the real pages, before </body>: counts what reached them.
LOGIN_TAIL = '''<script>
document.getElementById('instance').addEventListener('input', function (e) {
  document.body.dataset.heard = e.target.value;
});
</script>'''.encode()

ANSWER = 'https://thread.example/@me/123'
TITLE = 'Ask me about GNOME'
OWN_PATH = '@mauriciobc@ursal.zone'  # what /settings holds as the url
LONG_ANSWER = 'https://thread.example/' + 'a' * 2100


def card(inner, answered=True):
    """One cell of Card.tsx / QuestionsGrid.tsx: a card with its footer.
    An anchor given no className gets "undefined" from Card's
    cloneElement, as the real markup does."""
    return (
        '<div class="cell"><div class="card"><div class="card-content">'
        '<span class="%s">A question</span></div><footer class="card-footer">'
        '<time class="card-footer-item" datetime="2026-11-01T10:00:00.000Z">'
        '1 Nov</time>%s<button type="button" class="undefined '
        'card-footer-item">Arquivar</button></footer></div></div>'
        % ('card-content-normal' if answered else 'card-content-bold',
           inner))


def link(ident, href, text='Ver postagem', target=True):
    return ('<a id="%s" href="%s"%s class="undefined card-footer-item">%s'
            '</a>' % (ident, href, ' target="_blank"' if target else '',
                      text))


# The navbar of NavBar.tsx with the ShareButton island's markup inside it.
# `copied` counts the page's own handler, the one that would copy the link
# to the clipboard.
NAVBAR = (
    '<nav class="navbar" role="navigation"><div class="navbar-brand">'
    '<a class="navbar-item" href="/">Na Real</a></div>'
    '<div id="navbarMenu" class="navbar-menu"><div class="navbar-start">'
    '</div><div class="navbar-end"><div class="navbar-item">'
    '<span id="whoami">Mauricio (<a href="/logout">Deslogar</a>)</span>'
    '</div>'
    '<div class="navbar-item"><button type="button" class="button" '
    'id="share"><span id="share-label">Compartilhar</span></button></div>'
    '</div></div></nav>')
NAVBAR_TAIL = (
    '<script>document.body.dataset.copied = "0";'
    'document.getElementById("share").addEventListener("click", function () {'
    ' document.body.dataset.copied = String(+document.body.dataset.copied '
    '+ 1); });'
    'document.getElementById("other-button").addEventListener("click", function () {'
    ' document.body.dataset.decoy = "1"; });</script>')
DECOY = '<button type="button" class="button" id="other-button">Other</button>'


def settings_page(url=OWN_PATH):
    """/settings as settings.tsx renders it: the identification is the
    disabled `url` field, the question title its own field."""
    return ('<!doctype html><html><head><meta charset="utf-8"><title>'
            'Settings</title></head><body>%s<main><form method="POST">'
            '<input name="url" value="%s" disabled>'
            '<input name="question_title" type="text" value="%s">'
            '</form>%s</main>%s</body></html>'
            % (NAVBAR, url, TITLE, DECOY, NAVBAR_TAIL)).encode()


def grid(title):
    """The dashboard or the archive as QuestionsGrid renders it, plus the
    cases the plugin must leave alone."""
    cells = [
        card(link('answer', ANSWER)),
        card('<a id="nested" href="%s" target="_blank" class="undefined '
             'card-footer-item"><b id="inner">Ver</b> postagem</a>'
             % ANSWER),
        card(link('reply', '/question/abc', 'Responder', target=False),
             answered=False),
        card(link('decoy', '/dashboard')),
        card(link('mailto', 'mailto:a@b.example')),
        card(link('script', 'javascript:void(0)')),
        card(link('long', LONG_ANSWER)),
    ]
    return ('<!doctype html><html><head><meta charset="utf-8"><title>%s'
            '</title></head><body>%s<main><section class="section"><h2 '
            'class="title">%s</h2><div class="fixed-grid has-1-cols-mobile '
            'has-3-cols-tablet"><div class="grid">%s</div></div></section>'
            '%s</main><footer><a id="outside" href="https://thread.example/'
            'footer" target="_blank">credit</a></footer>%s</body></html>'
            % (title, NAVBAR, title, ''.join(cells), DECOY,
               NAVBAR_TAIL)).encode()


BARE = b'<!doctype html><title>bare</title><body><p>Nothing here</p></body>'

class Server:
    paths = []
    posts = []
    accepts = []
    session = False  # whether /dashboard and /settings have a signed-in user
    bare = False  # every page without the elements the plugin uses
    prefilled_login = False
    cookie = 'sid=ok'  # what the dashboard sets and /settings wants back
    settings_status = 200  # what /settings answers when signed in
    settings_url = OWN_PATH  # its `url` field
    settings_drop = False  # close the connection instead of answering
    own_status = 200  # what a user's own page (/@...) answers
    lag = 0.0  # seconds a user's own page takes to answer


class _Handler(http.server.BaseHTTPRequestHandler):
    def reply(self, status, body=b'', location=None, cookie=False):
        self.send_response(status)
        if cookie:
            self.send_header('Set-Cookie', Server.cookie + '; Path=/')
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        if location:
            self.send_header('Location', location)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Server.paths.append(self.path)
        Server.accepts.append(self.headers.get('Accept'))
        path = self.path
        if path == '/dashboard':
            if not Server.session:
                self.reply(302, location='/login')
            else:
                self.reply(200, BARE if Server.bare else grid('Perguntas'),
                           cookie=True)
        elif path == '/archived':
            self.reply(200, BARE if Server.bare else grid('Arquivadas'))
        elif path == '/settings':
            # Gated by the cookie, as the real one is by its session.
            if not Server.session \
                    or Server.cookie not in (self.headers.get('Cookie') or ''):
                self.reply(302, location='/login')
            elif Server.settings_drop:
                self.close_connection = True
            else:
                self.reply(Server.settings_status,
                           BARE if Server.bare
                           else settings_page(Server.settings_url))
        elif path == '/login':
            body = LOGIN_HTML
            if Server.bare:
                self.reply(200, BARE)
                return
            if Server.prefilled_login:
                body = body.replace(
                    b'<input id="instance"',
                    b'<input value="@me@else.example" id="instance"')
            self.reply(200, body.replace(b'</body>', LOGIN_TAIL + b'</body>'))
        elif path.startswith('/@'):
            time.sleep(Server.lag)
            if Server.own_status != 200:
                self.reply(Server.own_status, b'unavailable')
            else:
                self.reply(200, ASK_HTML)
        else:
            self.reply(404, b'Not Found')

    def do_POST(self):
        Server.posts.append(self.path)
        self.reply(200, b'posted')

    def log_message(self, *_args):
        pass


def serve(handler):
    httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, 'http://127.0.0.1:%d' % httpd.server_address[1]


httpd, SERVER = serve(_Handler)


class _Detour(http.server.BaseHTTPRequestHandler):
    """The user's own instance during the OAuth detour: it too has a
    /login with an instance field, which must be left alone."""

    def do_GET(self):
        body = (b'<!doctype html><title>Your instance</title>'
                b'<input id="instance">')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


detour, DETOUR = serve(_Detour)
probe = socket.socket()
probe.bind(('127.0.0.1', 0))
DEAD = 'http://127.0.0.1:%d' % probe.getsockname()[1]
probe.close()


class Host(Gtk.Window):
    def __init__(self):
        super().__init__()
        self.navigation_view = object()  # what pagenav.window_of looks for
        self.client = object()  # what pagenav.client_of looks for
        self.web_slot = Adw.Bin()
        self.web_split = Adw.OverlaySplitView(
            sidebar=self.web_slot, content=Gtk.Button(label='timeline'),
            show_sidebar=False, sidebar_position=Gtk.PackType.END,
            collapsed=False, min_sidebar_width=360, max_sidebar_width=720,
            sidebar_width_fraction=0.45, enable_show_gesture=False)
        self.set_default_size(1000, 600)
        self.set_child(self.web_split)
        webcolumn.watch_split(self.web_split)


Adw.init()
window = Host()
window.present()
spin(until=lambda: window.get_mapped() and window.get_width() > 0,
     seconds=5)
anchor = window.web_split.get_content()

toasts, opened, resolved, launched = [], [], [], []
real_pages = (feedback.show_toast, linkrouting.open_link,
              linkrouting.resolve_uri, textemoji.launch_uri)
feedback.show_toast = lambda widget, text, *a, **k: toasts.append(
    (widget, text))
linkrouting.resolve_uri = lambda _w, _c, uri: resolved.append(uri)
textemoji.launch_uri = lambda _w, uri: launched.append(uri)


def stub_open_link():
    """Record what a panel hands to open_link instead of routing it."""
    linkrouting.open_link = lambda widget, url, **k: opened.append(url)


def restore_open_link():
    linkrouting.open_link = real_pages[1]


settings = appsettings.settings()
settings.set_boolean('open-links-in-app', True)
settings.set_boolean('aggressive-resolving', False)
settings.set_boolean('web-javascript', True)
ME = {'id': '987654', 'acct': 'me', 'username': 'me',
      'instance': 'https://mastodon.social', 'token': 'T'}
accounts.save_account(settings, ME)

host = pluginhost.host()
started = host.start()
shipped = host.engine.get_plugin_info('babble_nareal') is not None
host.set_enabled('babble_nareal', True)
loaded = ('babble_nareal' in host.contexts
           and 'babble_nareal' not in host.errors)
module = sys.modules['babble_nareal']
context = host.contexts['babble_nareal']
main_ext = host.main_extensions['babble_nareal'][0]
link_ext = host.link_extensions['babble_nareal'][0]
context.settings.set('server', SERVER)

# Both Context.compose and Panel.compose end in pluginmenu.compose: record
# what reaches the composer instead of opening one.
composed = []
real_compose = pluginmenu.compose
pluginmenu.compose = lambda widget, text: composed.append((widget, text))


def column():
    return window.web_slot.get_child()


def panel_now():
    found = column()
    return found.panel if found is not None else None


def settled(uri):
    return spin(until=lambda: column() is not None
                and not column().view.is_loading()
                and column().view.get_uri() == uri, seconds=15)


def run(script, panel=None):
    panel = panel or panel_now()
    out = []
    panel.run_script(script, lambda result, error: out.append(
        (result, error)))
    spin(until=lambda: out, seconds=10)
    return out[0] if out else ('no callback', None)


def dom(expression):
    return run(expression)[0]


def wait_dom(expression, expected, seconds=6):
    spin(until=lambda: dom(expression) == expected, seconds=seconds)
    return dom(expression) == expected


def menu():
    """The main menu's plugin entries, as the window builds them."""
    return pluginmenu.entries(window)


def open_questions():
    menu()[0][1]()


def share_link():
    menu()[1][1]()


INVITE = 'Ask me anything, anonymously, on Na Real:'


def invitation(path, tag='nareal'):
    return '%s\n%s%s\n\n#%s' % (INVITE, SERVER, path, tag)


def shut():
    found = column()
    if found is not None:
        found.close_column()
    spin(0.1)


def no_panel_for(seconds=1.0):
    spin(seconds)
    return column() is None


timer_calls = []


real_timers = (GLib.timeout_add, GLib.timeout_add_seconds, GLib.idle_add)


LINK = '/@me@mastodon.social'
