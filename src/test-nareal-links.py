#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The links the Na Real plugin claims, and the settings page.

Exits 77 (skip) without a display, WebKitGTK 6.0 or libpeas 2."""

import os
# Never the user's real dconf: a test that builds a window restores the
# real stored account, and one that finds no keyring entry forgets it.
os.environ.setdefault('GSETTINGS_BACKEND', 'memory')
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

if not os.environ.get('DISPLAY') and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

from src import pluginspage  # noqa: E402

from testkit import narealplugin as kit  # noqa: E402
from testkit.narealplugin import (ANSWER,
    Adw,
    Gtk,
    ME,
    SERVER,
    Server,
    accounts,
    anchor,
    check,
    column,
    context,
    dom,
    host,
    launched,
    link_ext,
    linkrouting,
    module,
    no_panel_for,
    open_questions,
    opened,
    panel_now,
    resolved,
    restore_open_link,
    run,
    settings,
    settled,
    shut,
    spin,
    stub_open_link,
    toasts,
    wait_dom)  # noqa: E402
import json

# --- Links the plugin claims ----------------------------------------------

check('the link extension names the server’s origin',
      list(link_ext.origins()) == [SERVER])
shut()
del resolved[:], launched[:]
linkrouting.open_link(anchor, SERVER + '/login', resolve=True)
check('a claimed link opens an attached panel, with no fediverse routing',
      settled(SERVER + '/login') and resolved == [] and launched == []
      and context.data.get('panel') is panel_now()
      and panel_now().origin == SERVER)
check('and that panel is scripted like any other',
      wait_dom("document.getElementById('instance').value",
               'mastodon.social'))
shut()
linkrouting.open_link(anchor, SERVER + '/@alice@mastodon.social', resolve=True)
check('a /@user@host link on the server is the plugin’s, not Babble’s',
      settled(SERVER + '/@alice@mastodon.social') and resolved == [])
shut()
other = 'https://other.example/@user@host.tld'
linkrouting.open_link(anchor, other, resolve=True)
check('a link to another origin is routed as before',
      resolved == [other] and column() is None)
del resolved[:]
settings.set_boolean('open-links-in-app', False)
linkrouting.open_link(anchor, SERVER + '/login')
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
linkrouting.open_link(anchor, SERVER + '/@alice@mastodon.social', resolve=True)
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


if kit.failures:
    print('%d checks failed' % len(kit.failures))
    sys.exit(1)
print('all nareal links checks passed')
