#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composer transitions as data: builders, refusals and free-check."""
import gc
import os
import sys
import tempfile

_cache = tempfile.TemporaryDirectory(prefix='babble-compose-transition-')
os.environ['XDG_CACHE_HOME'] = _cache.name
os.environ['GSETTINGS_BACKEND'] = 'memory'

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(os.path.abspath(srcdir))
builddir = os.environ.get('BABBLE_BUILDDIR') or os.path.join(root, 'build')
sys.path.insert(0, root)
os.environ.setdefault('GSETTINGS_SCHEMA_DIR', os.path.join(builddir, 'data'))

from src import composetransition as ct  # noqa: E402

failures = []


def check(name, condition):
    print(('ok - ' if condition else 'FAIL - ') + name)
    if not condition:
        failures.append(name)


def is_keep(value):
    return value is ct.KEEP


# --- Builders match §4.2 -----------------------------------------------

reply = ct.reply('42', 'alice', 'unlisted',
                 parent={'spoiler_text': 'cw'})
check('reply pushes and prefills handle',
      reply.stack is ct.PUSH and reply.text == '@alice '
      and reply.visibility == 'unlisted'
      and reply.in_reply_to_id == '42'
      and reply.cw == (True, 'cw')
      and reply.thread_lookup == ('42', None, False))
check('reply keeps media poll language',
      is_keep(reply.media) and is_keep(reply.poll)
      and is_keep(reply.language))

new_msg = ct.reply(None, '', 'direct')
check('new message is push-if-not-empty with keep text',
      new_msg.stack is ct.PUSH_IF_NOT_EMPTY and is_keep(new_msg.text)
      and new_msg.in_reply_to_id is None
      and new_msg.thread_lookup is None
      and new_msg.visibility == 'direct')

mention = ct.mention({'acct': 'bob@example'}, direct=False)
check('mention pushes @acct and clears reply id',
      mention.stack is ct.PUSH and mention.text == '@bob@example '
      and mention.in_reply_to_id is None and is_keep(mention.visibility))

dm = ct.mention({'username': 'carol'}, direct=True)
check('direct mention sets visibility direct',
      dm.visibility == 'direct' and dm.text == '@carol ')

status = {
    'id': '9', 'text': 'hello', 'spoiler_text': 's',
    'visibility': 'private', 'sensitive': True,
    'media_attachments': [{'id': 'm1'}, {'id': 'm2'}],
}
edit = ct.edit(status)
check('edit clears stack and shows existing media',
      edit.stack is ct.CLEAR and edit.text == 'hello'
      and edit.cw == (True, 's') and edit.visibility == 'private'
      and edit.editing_id == '9'
      and edit.media == (True, (), ({'id': 'm1'}, {'id': 'm2'}))
      and edit.poll is ct.CLEAR
      and edit.scheduled_fields == {'sensitive': True}
      and edit.thread_lookup == ('9', status, True)
      and is_keep(edit.language))

redraft = ct.redraft(status)
check('redraft keeps media ids pending without rows',
      redraft.stack is ct.CLEAR
      and redraft.media == (True, ('m1', 'm2'), ())
      and redraft.editing_id is ct.KEEP
      and redraft.thread_lookup is None)

quote = ct.quote({'id': 'q1'})
check('quote clears stack and sets quoted id only',
      quote.stack is ct.CLEAR and is_keep(quote.text)
      and quote.quoted_status_id == 'q1'
      and quote.in_reply_to_id is None
      and is_keep(quote.visibility) and is_keep(quote.media))

prefill = ct.prefill('shared text')
check('prefill is push-if-not-empty with the text',
      prefill.stack is ct.PUSH_IF_NOT_EMPTY
      and prefill.text == 'shared text'
      and prefill.in_reply_to_id is None
      and is_keep(prefill.cw) and is_keep(prefill.visibility))

scheduled = ct.scheduled(
    {'id': 's1', 'params': {
        'text': 'later', 'spoiler_text': '', 'visibility': 'public',
        'language': 'de', 'sensitive': False,
        'quote_approval_policy': 'followers',
        'quoted_status_id': 'q', 'in_reply_to_id': 7,
        'poll': {'options': ['a', 'b'], 'expires_in': 3600,
                 'multiple': False, 'hide_totals': True},
        'media_ids': ['mid']}},
    max_poll_options=4, at='2099-01-01T00:00:00.000Z', on_done=None)
check('scheduled fills text poll language and triple',
      scheduled.stack is ct.CLEAR and scheduled.text == 'later'
      and scheduled.language == 'de'
      and scheduled.in_reply_to_id == '7'
      and scheduled.quoted_status_id == 'q'
      and scheduled.media == (True, ('mid',), ())
      and scheduled.poll['options'] == ['a', 'b']
      and scheduled.scheduled == ('s1', '2099-01-01T00:00:00.000Z', None)
      and scheduled.scheduled_fields['poll_flags']['hide_totals'] is True
      and scheduled.scheduled_fields['sensitive'] is False)

# KEEP table: every transition declares which fields stay
KEEP_FIELDS = (
    'text', 'cw', 'visibility', 'language', 'media', 'poll',
    'quoted_status_id', 'editing_id', 'scheduled', 'scheduled_fields',
)
expected_keep = {
    'reply': {'language', 'media', 'poll', 'quoted_status_id',
              'editing_id', 'scheduled', 'scheduled_fields'},
    'new_message': {'text', 'cw', 'language', 'media', 'poll',
                    'quoted_status_id', 'editing_id', 'scheduled',
                    'scheduled_fields'},
    'mention': {'cw', 'visibility', 'language', 'media', 'poll',
                'quoted_status_id', 'editing_id', 'scheduled',
                'scheduled_fields'},
    'direct_mention': {'cw', 'language', 'media', 'poll',
                       'quoted_status_id', 'editing_id', 'scheduled',
                       'scheduled_fields'},
    'edit': {'language', 'quoted_status_id', 'scheduled'},
    'redraft': {'language', 'quoted_status_id', 'editing_id', 'scheduled'},
    'quote': {'text', 'cw', 'visibility', 'language', 'media', 'poll',
              'editing_id', 'scheduled', 'scheduled_fields'},
    'prefill': {'cw', 'visibility', 'language', 'media', 'poll',
                'quoted_status_id', 'editing_id', 'scheduled',
                'scheduled_fields'},
    'scheduled': {'editing_id'},
}
samples = {
    'reply': reply,
    'new_message': new_msg,
    'mention': mention,
    'direct_mention': dm,
    'edit': edit,
    'redraft': redraft,
    'quote': quote,
    'prefill': prefill,
    'scheduled': scheduled,
}
for name, transition in samples.items():
    kept = {field for field in KEEP_FIELDS
            if getattr(transition, field) is ct.KEEP}
    check('KEEP fields for %s' % name, kept == expected_keep[name])

# --- scheduled refusals without a view ---------------------------------

for bad_poll in ({'options': ['a'] * 5, 'expires_in': 7200},
                 {'options': ['a', 'b'], 'expires_in': True},
                 {'options': ['a', 'a'], 'expires_in': 7200},
                 {'options': ['a', 'b'], 'expires_in': 7200, 'multiple': 1}):
    try:
        ct.scheduled({'id': 'bad', 'params': {'poll': bad_poll}},
                     max_poll_options=4, at=None)
        raised = False
    except ct.UnsupportedSettings:
        raised = True
    check('scheduled refuses bad poll %r' % (bad_poll,), raised)

try:
    ct.scheduled({'id': '', 'params': {}}, max_poll_options=4, at=None)
    raised = False
except ct.UnsupportedSettings:
    raised = True
check('scheduled refuses an empty id', raised)

try:
    ct.scheduled({'id': 'x', 'params': {'visibility': 'nope'}},
                 max_poll_options=4, at=None)
    raised = False
except ct.UnsupportedSettings:
    raised = True
check('scheduled refuses an unknown visibility', raised)

# --- The duplicated visibility codes stay equal to entities' -------------

import ast  # noqa: E402

_entities = ast.parse(open(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'entities.py'),
    encoding='utf-8').read())
_codes = next(
    ast.literal_eval(node.value) for node in _entities.body
    if isinstance(node, ast.Assign)
    and any(getattr(t, 'id', None) == 'VISIBILITIES' for t in node.targets))
check('the visibility codes equal entities.VISIBILITIES',
      ct._VISIBILITIES == frozenset(_codes))

# --- Free-check when a display is available ----------------------------

has_display = bool(os.environ.get('DISPLAY')
                   or os.environ.get('WAYLAND_DISPLAY'))
if has_display:
    import gi
    gi.require_version('Gtk', '4.0')
    gi.require_version('Adw', '1')
    from gi.repository import Adw, GLib
    from src.compose import BabbleComposeView, ComposeDialog
    from src.mastodon import MastodonClient

    Adw.init()
    client = MastodonClient('https://example.social')
    view = BabbleComposeView(client)
    dialog = ComposeDialog(client)
    view.start_reply('1', 'a')
    view.prefill_text('shared')
    check('a prefill after a reply leaves no reply target or thread',
          view.in_reply_to_id is None and view._thread is None)
    view.reset()
    try:
        refused = view.start_scheduled(None) is False
    except Exception:  # noqa: BLE001 - the regression was an AttributeError
        refused = False
    check('a scheduled payload that is not an object is refused, not raised',
          refused)
    view.reset()
    view_ref = view.weak_ref()
    dialog_ref = dialog.weak_ref()
    for starter in (
            lambda: view.start_reply('1', 'a'),
            lambda: view.start_mention({'acct': 'm'}),
            lambda: view.start_mention({'acct': 'm'}, direct=True),
            lambda: view.start_edit({'id': 'e', 'text': 't'}),
            lambda: view.start_redraft({'id': 'r', 'text': 't'}),
            lambda: view.start_quote({'id': 'q'}),
            lambda: view.prefill_text('p'),
            lambda: view.start_scheduled({'id': 's', 'params': {'text': ''}}),
    ):
        starter()
        view.reset()
    del view, dialog, starter, client
    gc.collect()
    # Drain a few idle iterations so GObject dispose can run.
    ctx = GLib.MainContext.default()
    for _ in range(20):
        while ctx.pending():
            ctx.iteration(False)
        gc.collect()
    check('compose view is freed after transitions',
          view_ref() is None)
    check('compose dialog is freed after transitions',
          dialog_ref() is None)
else:
    check('free-check skipped without a display', True)

if failures:
    print('%d failures' % len(failures))
    sys.exit(1)
print('ok - compose transition complete')
