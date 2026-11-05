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

import ast
import gc
import http.server
import json
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

from gi.repository import Adw, GLib, Gio, GObject, Gtk  # noqa: E402

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

from src import accounts, pages, pluginhost, pluginmenu  # noqa: E402
from src import pluginspage  # noqa: E402
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
        self.navigation_view = object()  # what pages.window_of looks for
        self.client = object()  # what pages.client_of looks for
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
real_pages = (pages.show_toast, pages.open_link, pages.resolve_uri,
              textemoji.launch_uri)
pages.show_toast = lambda widget, text, *a, **k: toasts.append(
    (widget, text))
pages.resolve_uri = lambda _w, _c, uri: resolved.append(uri)
textemoji.launch_uri = lambda _w, uri: launched.append(uri)


def stub_open_link():
    """Record what a panel hands to open_link instead of routing it."""
    pages.open_link = lambda widget, url, **k: opened.append(url)


def restore_open_link():
    pages.open_link = real_pages[1]


settings = pages.settings()
settings.set_boolean('open-links-in-app', True)
settings.set_boolean('aggressive-resolving', False)
settings.set_boolean('web-javascript', True)
ME = {'id': '987654', 'acct': 'me', 'username': 'me',
      'instance': 'https://mastodon.social', 'token': 'T'}
accounts.save_account(settings, ME)

host = pluginhost.host()
check('the host starts', host.start())
check('the Na Real plugin is shipped and found',
      host.engine.get_plugin_info('babble_nareal') is not None)
host.set_enabled('babble_nareal', True)
check('it loads', 'babble_nareal' in host.contexts
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


# --- The server setting --------------------------------------------------

check('the default server is the main Na Real instance',
      module.DEFAULT_SERVER == 'https://na-real.bolha.dev')
normalize = module.normalize_server
check('a bare host name is taken as https, and a slash dropped',
      normalize('na-real.example/') == 'https://na-real.example')
check('https is accepted',
      normalize('https://x.example') == 'https://x.example')
check('plain http is accepted on this machine only',
      normalize('http://localhost:8000') == 'http://localhost:8000'
      and normalize('http://127.0.0.1:1') == 'http://127.0.0.1:1'
      and normalize('http://[::1]:1') == 'http://[::1]:1'
      and normalize('http://x.example') is None)
check('other schemes and nothing are refused',
      normalize('ftp://x.example') is None and normalize('') is None
      and normalize(None) is None)

# --- No timers, no polling, no stored widgets (the source) ----------------

with open(module.__file__, encoding='utf-8') as source:
    tree = ast.parse(source.read())
named = {node.attr for node in ast.walk(tree)
         if isinstance(node, ast.Attribute)}
named |= {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
check('the plugin names no timer or idle source',
      not named & {'timeout_add', 'timeout_add_seconds', 'idle_add',
                   'GLib', 'Timer', 'sleep'})
timer_calls = []
real_timers = (GLib.timeout_add, GLib.timeout_add_seconds, GLib.idle_add)


def watched(real):
    def call(*args, **kwargs):
        caller = sys._getframe(1).f_code.co_filename
        if caller == module.__file__:
            timer_calls.append(caller)
        return real(*args, **kwargs)
    return call


GLib.timeout_add, GLib.timeout_add_seconds, GLib.idle_add = (
    watched(real_timers[0]), watched(real_timers[1]),
    watched(real_timers[2]))

# --- The main menu ----------------------------------------------------------

check('the main menu has the two entries, in this order',
      [label for label, _activate in menu()]
      == ['My _Questions', '_Share My Questions Link'])
check('and the plugin has no post menu: no class, nothing registered',
      host.menu_extensions['babble_nareal'] == []
      and not any(isinstance(v, type)
                  and issubclass(v, module.plugin.PostMenuExtension)
                  for v in vars(module).values())
      and pluginhost.menu_items(anchor, {
          'id': '1', 'account': {'acct': 'alice'}, 'content': '<p>hi</p>',
          'url': 'https://mastodon.social/@x/1'}, 'https://mastodon.social')
      == [])

# My Questions.
Server.paths.clear()
open_questions()
check('My Questions goes to the dashboard and on to the login page',
      settled(SERVER + '/login')
      and Server.paths[:2] == ['/dashboard', '/login'])
check('the panel is on the Na Real server, and kept as the current one',
      column().panel.origin == SERVER
      and context.data.get('panel') is panel_now())
shut()

# --- Share My Questions Link ------------------------------------------------

LINK = '/@me@mastodon.social'
del toasts[:]
Server.paths.clear()
Server.accepts.clear()
share_link()
check('an own page that answers 200 opens the composer, once, with the '
      'invitation, the link and the hashtag',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, invitation(LINK))])
spin(0.5)
check('the composer is in the menu’s window, and nothing else happened',
      composed[0][0] is window and len(composed) == 1 and toasts == []
      and no_panel_for(0.2))
check('the probe asked the user’s own page, for HTML',
      Server.paths == [LINK] and Server.accepts == ['text/html'])
del composed[:]

context.settings.set('hashtag', 'ask')
share_link()
check('the configured hashtag is the one the invitation ends with',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, invitation(LINK, 'ask'))])
context.settings.remove('hashtag')
del composed[:]

# The link is exact: the host in lower case, a plain @.
accounts.save_account(settings, {
    'id': '77', 'acct': 'Bob', 'username': 'Bob',
    'instance': 'https://Ursal.ZONE', 'token': 'T'})
Server.paths.clear()
share_link()
check('a mixed-case host is lower-cased, the @ is not percent-encoded',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, invitation('/@Bob@ursal.zone'))]
      and Server.paths == ['/@Bob@ursal.zone'])
check('no path was asked for with %40', not any('%40' in x
                                                  for x in Server.paths))
accounts.save_account(settings, ME)
del composed[:]

# The user has never signed in to Na Real: its own page is a 404.
Server.own_status = 404
Server.session = False
Server.paths.clear()
share_link()
check('a 404 toasts that the user is not on Na Real yet, in the window',
      spin(until=lambda: toasts, seconds=5)
      and toasts == [(window, 'You are not on Na Real yet. Sign in from '
                      'My Questions first.')])
check('and opens the dashboard, not the composer',
      settled(SERVER + '/login') and composed == []
      and Server.paths[:3] == [LINK, '/dashboard', '/login']
      and context.data.get('panel') is panel_now())
shut()

for status in (500, 503):
    Server.own_status = status
    share_link()
    check('a %d still opens the composer with the invitation' % status,
          spin(until=lambda: composed, seconds=5)
          and composed == [(window, invitation(LINK))])
    del composed[:]
Server.own_status = 200

del toasts[:]
context.settings.set('server', DEAD)
share_link()
check('no answer at all (status 0) still opens the composer',
      spin(until=lambda: composed, seconds=5)
      and composed == [(window, '%s\n%s%s\n\n#nareal' % (INVITE, DEAD, LINK))]
      and toasts == [] and column() is None)
del composed[:]
context.settings.set('server', SERVER)

settings.set_string('active-account', '')
Server.paths.clear()
del toasts[:]
share_link()
check('with nobody signed in the user is told, and nothing else happens',
      toasts == [(window, 'Sign in to Babble first.')]
      and spin(0.5) and composed == [] and Server.paths == []
      and column() is None)
accounts.save_account(settings, ME)
del toasts[:]

# --- The login form -------------------------------------------------------

Server.posts.clear()
open_questions()
settled(SERVER + '/login')
check('the instance field gets the account’s host',
      wait_dom("document.getElementById('instance').value",
               'mastodon.social'))
check("the page's own handler heard the input event",
      dom('document.body.dataset.heard') == 'mastodon.social')
spin(0.8)
check('the login form was never submitted', Server.posts == [])
shut()

Server.prefilled_login = True
open_questions()
settled(SERVER + '/login')
spin(0.8)
check('a field that already has a value is left alone',
      dom("document.getElementById('instance').value") == '@me@else.example'
      and dom('document.body.dataset.heard') is None)
check('and nothing was submitted either', Server.posts == [])
shut()
Server.prefilled_login = False

# The OAuth detour: a login page on another origin is left alone.
open_questions()
settled(SERVER + '/login')
wait_dom("document.getElementById('instance').value", 'mastodon.social')
del toasts[:]
column().view.load_uri(DETOUR + '/login')
settled(DETOUR + '/login')
spin(1.0)
marker = []
column().evaluate('test-reader',
                  "String(document.getElementById('instance').value)",
                  lambda value, error: marker.append(
                      value.to_string() if value is not None else error))
spin(until=lambda: marker, seconds=10)
check('another origin’s login page is not filled, and nothing is said',
      marker == [''] and toasts == [] and 'babble_nareal' in host.contexts)
shut()

# Missing selectors do nothing: the pages without what the plugin uses.
Server.bare = True
Server.session = True
open_questions()
settled(SERVER + '/dashboard')
bare_panel = panel_now()
outcomes = []
for path in ('/login', '/dashboard', '/archived', '/settings'):
    for script in module.scripts_for(context, path):
        outcomes.append(run(script, bare_panel))
check('scripts on pages without their elements neither throw nor do '
      'anything',
      all(error is None for _result, error in outcomes)
      and [result for result, _e in outcomes]
      == ['skipped', 'installed', 'installed', 'installed', 'installed',
          'installed'])
settings.set_string('active-account', '')
check('with nobody signed in the login page gets no script',
      module.scripts_for(context, '/login') == [])
accounts.save_account(settings, ME)
check('there is no script for a page that has none to run',
      module.scripts_for(context, '/@me@mastodon.social') == []
      and module.scripts_for(context, '/') == [])
opened_before = len(opened)
check('and the page is untouched: no message, no toast, no composer',
      run('document.body.click(); document.body.innerHTML.length')[1]
      is None and spin(0.4) and len(opened) == opened_before
      and toasts == [] and composed == [])
shut()
Server.bare = False
Server.session = False

# JavaScript off: one toast per panel, not per load.
settings.set_boolean('web-javascript', False)
del toasts[:]
open_questions()
settled(SERVER + '/login')
panel_a = panel_now()
check('with JavaScript off the user is told, in the panel’s own column',
      spin(until=lambda: toasts, seconds=5)
      and toasts[0][1] == 'Na Real needs JavaScript. Turn it on in '
      'Preferences.' and toasts[0][0] is column())
panel_a.load_uri(SERVER + '/login')
settled(SERVER + '/login')
spin(0.5)
panel_a.load_uri(SERVER + '/login?again')
settled(SERVER + '/login?again')
spin(0.5)
check('and only once for that panel, however many pages it loads',
      len(toasts) == 1)
open_questions()
settled(SERVER + '/login')
check('a new panel is told again',
      spin(until=lambda: len(toasts) == 2, seconds=5))
shut()
settings.set_boolean('web-javascript', True)

# --- Links the plugin claims ----------------------------------------------

check('the link extension names the server’s origin',
      list(link_ext.origins()) == [SERVER])
shut()
del resolved[:], launched[:]
pages.open_link(anchor, SERVER + '/login', resolve=True)
check('a claimed link opens an attached panel, with no fediverse routing',
      settled(SERVER + '/login') and resolved == [] and launched == []
      and context.data.get('panel') is panel_now()
      and panel_now().origin == SERVER)
check('and that panel is scripted like any other',
      wait_dom("document.getElementById('instance').value",
               'mastodon.social'))
shut()
pages.open_link(anchor, SERVER + '/@alice@mastodon.social', resolve=True)
check('a /@user@host link on the server is the plugin’s, not Babble’s',
      settled(SERVER + '/@alice@mastodon.social') and resolved == [])
shut()
other = 'https://other.example/@user@host.tld'
pages.open_link(anchor, other, resolve=True)
check('a link to another origin is routed as before',
      resolved == [other] and column() is None)
del resolved[:]
settings.set_boolean('open-links-in-app', False)
pages.open_link(anchor, SERVER + '/login')
check('with open-links-in-app off it is not claimed',
      launched == [SERVER + '/login'] and no_panel_for(0.5))
settings.set_boolean('open-links-in-app', True)
del launched[:]

# A server that is the account's own instance claims nothing.
accounts.save_account(settings, {
    'id': '5', 'acct': 'you', 'username': 'you', 'instance': SERVER,
    'token': 'T'})
check('the plugin guard: no origins when the server is the account’s own',
      list(link_ext.origins()) == [])
pages.open_link(anchor, SERVER + '/@alice@mastodon.social', resolve=True)
check('and the link is routed as an ordinary one',
      resolved == [SERVER + '/@alice@mastodon.social']
      and no_panel_for(0.5))
del resolved[:]

# Its settings, here with the account on that very server.
plugins_window = Adw.Window(default_width=600, default_height=700)
plugins_window.set_content(Gtk.Label())
plugins_window.present()
plugins = pluginspage.present_plugins(plugins_window)
row = [r for r in plugins.rows if r.plugin_id == 'babble_nareal'][0]
check('the Plugins page offers its settings',
      row.settings_button.get_visible())
row.settings_button.emit('clicked')
preferences = [ext for slot in host.slots if slot.plugin_id
               for ext, *_rest in slot.extensions.get('babble_nareal', [])]
page = preferences[0]
check('they show the server in use', page.server.get_text() == SERVER)
page.server.set_text(SERVER + '/')
page.server.emit('apply')
check('the signed-in account’s own server is refused, with a message',
      page.error.get_visible() and page.error.get_label()
      == 'That is your own server. Use the address of a Na Real server.'
      and context.settings.get('server') == SERVER)
page.server.set_text('http://na-real.example')
page.server.emit('apply')
check('plain http to another machine is refused',
      page.error.get_visible() and page.error.get_label()
      == 'Use the server’s https:// address'
      and context.settings.get('server') == SERVER)
accounts.save_account(settings, ME)  # back on mastodon.social
page.server.set_text('na-real.example/')
page.server.emit('apply')
check('a new server is saved, as https, and the user told',
      context.settings.get('server') == 'https://na-real.example'
      and not page.error.get_visible()
      and page.server.get_text() == 'https://na-real.example'
      and toasts and toasts[-1][1] == 'Na Real server saved')
page.server.set_text('mastodon.social')
page.server.emit('apply')
check('the account’s own server is refused after a switch too',
      page.error.get_visible()
      and context.settings.get('server') == 'https://na-real.example')
settings.set_string('active-account', '')
page.server.set_text('mastodon.social')
page.server.emit('apply')
check('with nobody signed in nothing is protected',
      context.settings.get('server') == 'https://mastodon.social'
      and not page.error.get_visible())
accounts.save_account(settings, ME)
page.server.set_text('')
page.server.emit('apply')
check('empty resets to the default',
      context.settings.get('server') is None
      and page.server.get_text() == module.DEFAULT_SERVER)
page.server.set_text('na-real.example')
page.server.emit('apply')
page.server.set_text(module.DEFAULT_SERVER)
page.server.emit('apply')
check('the default is not stored as a setting',
      context.settings.get('server') is None)
check('the settings have a server and a hashtag, and no prefill switch',
      not hasattr(page, 'prefill') and hasattr(page, 'hashtag'))
# The hashtag setting (banner).
check('the hashtag entry shows the default', page.hashtag.get_text()
      == 'nareal')
page.hashtag.set_text('two words')
page.hashtag.emit('apply')
check('a hashtag of two words is refused',
      page.error.get_visible() and context.settings.get('hashtag') is None)
page.hashtag.set_text('#Ask')
page.hashtag.emit('apply')
check('another hashtag is saved lower-cased, without the #, and the user '
      'told', context.settings.get('hashtag') == 'ask'
      and page.hashtag.get_text() == 'ask' and not page.error.get_visible()
      and toasts[-1][1] == 'Na Real hashtag saved')
page.hashtag.set_text('')
page.hashtag.emit('apply')
check('empty resets the hashtag to the default',
      context.settings.get('hashtag') is None
      and page.hashtag.get_text() == 'nareal')
page.hashtag.set_text('ask')
page.hashtag.emit('apply')
page.hashtag.set_text('NaReal')
page.hashtag.emit('apply')
check('the default hashtag is not stored as a setting',
      context.settings.get('hashtag') is None)
del preferences, page, row
plugins.close()
spin(until=lambda: plugins_window.get_visible_dialog() is None, seconds=3)
plugins_window.destroy()
context.settings.set('server', SERVER)

# --- The answer link on the dashboard and the archive ---------------------

FIRE = '''(function (id) {
  var a = document.getElementById(id);
  var seen = false;
  var handler = function (e) { seen = true; e.preventDefault(); };
  window.addEventListener('click', handler);
  var event = new MouseEvent('click', {bubbles: true, cancelable: true});
  a.dispatchEvent(event);
  window.removeEventListener('click', handler);
  return seen ? 'page' : (event.defaultPrevented ? 'plugin' : 'none');
})(%s)'''


def fire(ident):
    return dom(FIRE % json.dumps(ident))


def installed():
    return dom('!!window.__narealAnswers')


stub_open_link()
Server.session = True
for path, label in (('/dashboard', 'dashboard'), ('/archived', 'archive')):
    del opened[:]
    open_questions()  # Open Na Real: the dashboard
    settled(SERVER + '/dashboard')
    if path == '/archived':
        panel_now().load_uri(SERVER + path)
        settled(SERVER + path)
    check('the %s listener is installed' % label,
          spin(until=lambda: installed() is True, seconds=6))
    check('a click on an answer link is taken from the page on the %s'
          % label, fire('answer') == 'plugin')
    check('and its thread goes to Babble (%s)' % label,
          spin(until=lambda: opened, seconds=5) and opened == [ANSWER])
    check('the panel stays on the %s' % label,
          panel_now().is_open and panel_now().uri == SERVER + path)
    del opened[:]
    check('a click on something inside the link counts too (%s)' % label,
          fire('inner') == 'plugin'
          and spin(until=lambda: opened, seconds=5) and opened == [ANSWER])
    del opened[:]
    # Installing the script again does not add a second listener.
    run(module.scripts_for(context, path)[0])
    fire('answer')
    spin(0.6)
    check('the listener is installed once per page (%s)' % label,
          opened == [ANSWER])
    del opened[:]
    results = [fire(i) for i in ('decoy', 'mailto', 'script', 'reply',
                                 'outside')]
    spin(0.6)
    check('same-origin, non-http(s), unanswered and outside links are left '
          'to the page (%s)' % label,
          results == ['page'] * 5 and opened == [])
    check('an over-long link is taken but dropped by the host (%s)' % label,
          fire('long') == 'plugin' and spin(0.6) and opened == [])
    shut()
Server.session = False

# Messages the host must drop, sent through the real channel.
open_questions()
settled(SERVER + '/login')
del opened[:]
msg_panel = panel_now()
for bad in ('{answer: "ftp://x.example/a"}',
            '{answer: "javascript:alert(1)"}', '{answer: 5}',
            '{other: "https://thread.example/a"}', '"https://x.example/"',
            '["https://x.example/"]', 'null',
            '{answer: "' + SERVER + '/question/1"}',
            '{answer: "https://thread.example/a b"}',
            '{answer: "https://thread.example/' + 'a' * 2100 + '"}',
            '{answer: "http://["}'):
    run('window.webkit.messageHandlers.babble.postMessage(%s)' % bad,
        msg_panel)
spin(0.8)
check('non-http(s), non-string, malformed, spaced, over-long and '
      'same-origin messages are dropped silently',
      opened == [] and msg_panel.is_open
      and 'babble_nareal' in host.contexts)
run('window.webkit.messageHandlers.babble.postMessage('
    '{answer: "https://thread.example/ok"})', msg_panel)
check('while a good one is passed on',
      spin(until=lambda: opened, seconds=5)
      and opened == ['https://thread.example/ok'])
shut()
restore_open_link()

# --- The share button ---------------------------------------------------------

CLICK = '''(function (id) {
  var b = document.getElementById(id);
  var event = new MouseEvent('click', {bubbles: true, cancelable: true});
  b.dispatchEvent(event);
  return event.defaultPrevented ? 'prevented' : 'passed';
})(%s)'''


def click(ident):
    return dom(CLICK % json.dumps(ident))


def goto(path):
    """Show *path* of the fake in the open panel, once its scripts ran
    (the page sets `copied` last; `settled` alone can return before the
    new page replaced the old one)."""
    panel_now().load_uri(SERVER + path)
    settled(SERVER + path)
    return spin(until=lambda: dom('location.pathname') == path.split('?')[0]
                and dom('document.body.dataset.copied') == '0', seconds=8)


def open_dashboard():
    open_questions()
    spin(until=lambda: panel_now() is not None, seconds=5)
    return spin(
        until=lambda: dom('location.pathname') == '/dashboard'
        and dom('document.body.dataset.copied') == '0', seconds=8)


def own_link():
    return SERVER + '/' + OWN_PATH


def shared():
    return TITLE + '\n' + own_link()


def settings_requests():
    return Server.paths.count('/settings')


stub_open_link()
Server.session = True
for path, label in (('/dashboard', 'dashboard'), ('/archived', 'archive')):
    del composed[:]
    open_dashboard()
    if path != '/dashboard':
        goto(path)
    check('the share listener is installed on the %s' % label,
          spin(until=lambda: dom('!!window.__narealShare') is True,
               seconds=6))
    Server.paths.clear()
    check('the click on Compartilhar is stopped (%s)' % label,
          click('share') == 'prevented')
    check('the page’s own handler never ran, so nothing is copied (%s)'
          % label, dom('document.body.dataset.copied') == '0')
    check('Babble’s composer gets the title and the link read from the '
          'user’s settings, in the panel’s column (%s)' % label,
          spin(until=lambda: composed, seconds=6)
          and composed == [(column(), shared())])
    check('the settings were fetched, once (%s)' % label,
          settings_requests() == 1)
    del composed[:]
    check('the click on the label inside the button counts too (%s)'
          % label, click('share-label') == 'prevented'
          and spin(until=lambda: composed, seconds=6)
          and composed == [(column(), shared())])
    del composed[:]
    run(module.scripts_for(context, path)[-1])
    click('share')
    spin(0.8)
    check('the listener is installed once per page (%s)' % label,
          len(composed) == 1 and dom('document.body.dataset.copied') == '0')
    del composed[:], toasts[:]
    check('another button, and text in the navbar, are left to the page '
          '(%s)' % label,
          click('other-button') == 'passed' and click('whoami') == 'passed'
          and dom('document.body.dataset.decoy') == '1')
    spin(0.6)
    check('and open nothing (%s)' % label, composed == [] and toasts == [])
    shut()

# On the settings page itself the form is already in the DOM.
open_dashboard()
goto('/settings')
spin(until=lambda: dom('!!window.__narealShare') is True, seconds=6)
Server.paths.clear()
check('on /settings the click is stopped', click('share') == 'prevented'
      and dom('document.body.dataset.copied') == '0')
check('and the title and link come from the page, with no second request',
      spin(until=lambda: composed, seconds=6)
      and composed == [(column(), shared())] and spin(0.5)
      and settings_requests() == 0)
del composed[:]

# The settings cannot be read: the host builds the link from the account.
open_dashboard()
goto('/archived')
spin(until=lambda: dom('!!window.__narealShare') is True, seconds=6)
for what, setup in (
        ('a /settings that answers an error with the form in it',
         lambda: setattr(Server, 'settings_status', 500)),
        ('a /settings whose connection drops',
         lambda: setattr(Server, 'settings_drop', True)),
        ('a /settings with an empty identification',
         lambda: setattr(Server, 'settings_url', '')),
        ('a /settings that redirects to the login (the cookie expired)',
         lambda: setattr(Server, 'session', False))):
    Server.settings_status, Server.settings_drop = 200, False
    Server.settings_url, Server.session = OWN_PATH, True
    setup()
    check('%s falls back to the account’s own link' % what,
          click('share') == 'prevented'
          and spin(until=lambda: composed, seconds=6)
          and composed == [(column(), invitation(LINK))])
    del composed[:]
    spin(0.3)
    check('and only once (%s)' % what, composed == [])
Server.settings_status, Server.settings_drop = 200, False
Server.settings_url, Server.session = OWN_PATH, False
settings.set_string('active-account', '')
del toasts[:]
check('and with nobody signed in either the user is told',
      click('share') == 'prevented'
      and spin(until=lambda: toasts, seconds=6)
      and toasts == [(column(), 'Couldn’t read your Na Real link.')]
      and composed == [])
accounts.save_account(settings, ME)
del toasts[:]
shut()

# Messages the host must drop, and the good ones, sent through the real
# channel.
open_questions()
settled(SERVER + '/login')
msg_panel = panel_now()


def post(data):
    run('window.webkit.messageHandlers.babble.postMessage(%s)'
        % json.dumps(data), msg_panel)


del composed[:], toasts[:]
LONG_TEXT = 'x' * 501
for bad in ({'share': {'text': 'x', 'url': 'https://thread.example/@a'}},
            {'share': {'text': 'x', 'url': 'http://127.0.0.1:1/a'}},
            {'share': {'text': 'x', 'url': 'ftp://127.0.0.1/a'}},
            {'share': {'text': 'x', 'url': 'javascript:alert(1)'}},
            {'share': {'text': 'x', 'url': SERVER + '/a b'}},
            {'share': {'text': 'x', 'url': SERVER + '/a\tb'}},
            {'share': {'text': 'x', 'url': SERVER + '/' + 'a' * 2100}},
            {'share': {'text': LONG_TEXT, 'url': SERVER + '/a'}},
            {'share': {'text': 'a\x07b', 'url': SERVER + '/a'}},
            {'share': {'text': 'a\nb', 'url': SERVER + '/a'}},
            {'share': {'text': 5, 'url': SERVER + '/a'}},
            {'share': {'text': 'x', 'url': 5}},
            {'share': {'text': 'x'}},
            {'share': 'x'}, {'share': None}, {'share': [SERVER]},
            {'text': 'x', 'url': SERVER + '/a'}, [SERVER], 'share', None):
    post(bad)
spin(0.8)
check('a foreign origin, a bad scheme, whitespace, an over-long url or '
      'text, control characters and anything else are dropped silently',
      composed == [] and toasts == [] and msg_panel.is_open
      and 'babble_nareal' in host.contexts)
post({'share': {'text': 'x' * 500, 'url': SERVER + '/' + 'a' * 100}})
check('a text of exactly 500 characters is accepted',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), 'x' * 500 + '\n' + SERVER + '/' + 'a' * 100)])
del composed[:]
post({'share': {'text': '', 'url': SERVER + '/@a@b.example'}})
check('an empty text is left out, the link alone goes',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), SERVER + '/@a@b.example')])
del composed[:]
post({'share': {'url': SERVER + '/@a@b.example'}})
check('so is a missing one',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), SERVER + '/@a@b.example')])
del composed[:]
post({'share': {'text': 'Hello', 'url': SERVER + '/@a@b.example'}})
check('title and link are joined by a line break',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), 'Hello\n' + SERVER + '/@a@b.example')])
del composed[:]
post({'share': {}})
check('an empty share (the page could not read it) falls back to the '
      'account’s link',
      spin(until=lambda: composed, seconds=5)
      and composed == [(column(), invitation(LINK))])
shut()
restore_open_link()
Server.session = False

# --- The hashtag banner (its own commit) -----------------------------------

from src import hashtags  # noqa: E402


class _ApiClient:
    token = 'token'
    instance_info = None
    instance = 'https://example.org'

    def request_finish(self, result):
        return result

    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def banner_of(page):
    """The page's Na Real extension (its widget is ``.list``)."""
    for slot in host.slots:
        if slot.box_ref() is page.plugin_banners:
            for extension, *_rest in slot.extensions.get(
                    'babble_nareal', []):
                return extension
    return None


client = _ApiClient()
Server.paths.clear()
page = hashtags.HashtagPage(client, ['nareal'])
banner = banner_of(page)
check('the configured hashtag gets the banner',
      page.plugin_banners.get_visible() and banner.list.get_visible()
      and banner.row.get_title() == '#nareal')
other_page = hashtags.HashtagPage(client, ['python'])
check('any other hashtag gets none',
      not other_page.plugin_banners.get_visible()
      and banner_of(other_page) is not None
      and not hasattr(banner_of(other_page), 'list'))
mixed = hashtags.HashtagPage(client, ['NaReal'])
check('the tag is matched whatever its case',
      mixed.plugin_banners.get_visible())
spin(0.7)
check('the banner asks the network nothing', Server.paths == [])
context.settings.set('hashtag', 'ask')
ask_page = hashtags.HashtagPage(client, ['ask'])
old_page = hashtags.HashtagPage(client, ['nareal'])
check('the hashtag setting moves it',
      ask_page.plugin_banners.get_visible()
      and not old_page.plugin_banners.get_visible())
context.settings.remove('hashtag')
del ask_page, old_page, other_page, mixed

recorded = []
real_open_panel = context.open_panel
context.open_panel = lambda widget, url, title=None: (
    recorded.append((url, title)),
    real_open_panel(anchor, url, title=title))[1]
banner.button.emit('clicked')
check('the banner button opens the dashboard as Na Real',
      recorded == [(SERVER + '/dashboard', 'Na Real')]
      and settled(SERVER + '/login'))
check('and that panel is scripted like any other',
      wait_dom("document.getElementById('instance').value",
               'mastodon.social'))
del context.open_panel
shut()
page_ref = page.weak_ref()
del page, banner
check('the page is freed', freed(page_ref))

# --- What is left behind ---------------------------------------------------

open_questions()
settled(SERVER + '/login')
check('an open panel is the current handle',
      context.data.get('panel') is panel_now())
column().close_column()  # the user hides the column
check('on_close clears the plugin’s state',
      spin(until=lambda: 'panel' not in context.data, seconds=5))

open_questions()
settled(SERVER + '/login')
first = panel_now()
pages.open_link(anchor, SERVER + '/login?second', resolve=True)
settled(SERVER + '/login?second')
spin(0.5)
check('a replaced panel does not clear the newer one’s handle',
      not first.is_open and context.data.get('panel') is panel_now())
shut()

check('no stored widget: not in the shared data, nor on the extensions',
      not any(isinstance(v, Gtk.Widget) for v in context.data.values())
      and not any(isinstance(v, GObject.Object)
                  for ext in (main_ext, link_ext)
                  for v in vars(ext).values() if v is not ext.context))
frees = []
open_questions()
settled(SERVER + '/login')
column().weak_ref(lambda: frees.append(True))
pages.open_link(anchor, SERVER + '/login?third', resolve=True)
settled(SERVER + '/login?third')
spin(0.5)
shut()
for _attempt in range(5):
    gc.collect()
    spin(0.1)
check('the web column is freed once the panel is closed', frees == [True])
check('and no script of the plugin starts a timer in the page',
      not any(word in value for name, value in vars(module).items()
              if name.endswith('_SCRIPT') and isinstance(value, str)
              for word in ('setTimeout', 'setInterval')))

# Disabling the plugin.
open_questions()
settled(SERVER + '/login')
dead_panel = panel_now()
count = (len(toasts), len(opened), len(composed))
Server.lag = 0.8
share_link()  # a probe in flight that would compose
Server.own_status = 404
share_link()  # and one that would open the dashboard and toast
host.set_enabled('babble_nareal', False)
check('disabling the plugin closes its panel',
      not dead_panel.is_open and column() is None)
spin(1.8)
Server.lag = 0.0
Server.own_status = 200
check('a probe in flight never opens a panel, a toast or the composer '
      'afterwards', column() is None
      and (len(toasts), len(opened), len(composed)) == count)
check('the main menu has no entries once it is disabled', menu() == [])
del resolved[:]
pages.open_link(anchor, SERVER + '/@alice@mastodon.social', resolve=True)
check('and drops its claim on links',
      resolved == [SERVER + '/@alice@mastodon.social']
      and column() is None)
check('and nothing calls back',
      (len(toasts), len(opened), len(composed)) == count)
check('the plugin never used a timer', timer_calls == [])

GLib.timeout_add, GLib.timeout_add_seconds, GLib.idle_add = real_timers
pluginmenu.compose = real_compose
(pages.show_toast, pages.open_link, pages.resolve_uri,
 textemoji.launch_uri) = real_pages
host.shutdown()
window.destroy()
httpd.shutdown()
detour.shutdown()
if failures:
    print('%d failure(s)' % len(failures))
    sys.exit(1)
