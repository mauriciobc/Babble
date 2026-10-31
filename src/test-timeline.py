#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Renders the Babble timeline widgets with REAL data from
mastodon.social's anonymous public timeline, proving the row rendering
path used by the home timeline. Exits 77 (skip) when the network is
unreachable."""

import os
import sys
import time as time_module

# Same session bus as every other widget test; see test-searchprovider.py.
os.environ['GDK_DEBUG'] = ','.join(
    filter(None, (os.environ.get('GDK_DEBUG'), 'no-portals')))
os.environ.setdefault('NO_AT_BRIDGE', '1')
os.environ.setdefault('GTK_A11Y', 'none')

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')

from gi.repository import Adw, GLib, Gtk

if not os.environ.get('DISPLAY') \
        and not os.environ.get('WAYLAND_DISPLAY'):
    print('SKIP - no display available for widget rendering')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.mastodon import MastodonClient  # noqa: E402
from src.textemoji import InlineEmojiText, status_emoji_bank  # noqa: E402
from src.mediabox import BlurredPicture, snap_carousel_offset  # noqa: E402
from src.timelinedata import (BoostCarouselItem, StatusItem,  # noqa: E402
                              TimelineModel, group_boosts,
                              filter_replies_from_non_follows,
                              matching_tag)
from src.statusrow import StatusRow  # noqa: E402
from src.statusactions import StatusActions  # noqa: E402
from src.statusmenu import StatusMenuButton  # noqa: E402
from src.statusrun import StatusRun, _shell_stamp  # noqa: E402
from src.timelinepage import TimelinePage  # noqa: E402
from src.timelinebase import (decodable_attachment_url,  # noqa: E402
                              COLUMN_WIDTH)
from src.statustext import parse_content, relative_time  # noqa: E402

failures = []


def check(name, condition):
    if condition:
        print('ok - %s' % name)
    else:
        failures.append(name)
        print('FAIL - %s' % name)


# A failed home request must stay an error. It used to fall back to the
# public timeline, which replaced the reader's home with posts from
# accounts they do not follow.
class _HomeFailureClient:
    def __init__(self):
        self.public_requests = 0

    def timelines_home_async(self, *_args, **_kwargs):
        pass

    def timelines_home_finish(self, _result):
        raise GLib.Error('home failed', 'mastodon-http-error-quark', 500)

    def timelines_public_async(self, *_args, **_kwargs):
        self.public_requests += 1


failure_client = _HomeFailureClient()
failure_page = TimelinePage(failure_client, mode='home', autoload=False)
failure_page.on_page_loaded(failure_client, None)
check('a failed home request shows the error page',
      failure_page.stack.get_visible_child_name() == 'error')
check('a failed home request does not load the public timeline',
      failure_client.public_requests == 0)

# Unit checks (no display needed)
segments = parse_content('<p>Hello <a href="https://example.org">'
                         'world</a> &amp; friends</p><p>Second</p>')
check('content text preserved', any('Hello' in s['text'] for s in segments))
check('link href extracted',
      any(s['href'] == 'https://example.org' for s in segments))
check('no raw html leaks', all('<p>' not in s['text'] for s in segments))
check('relative time', relative_time('2020-01-01T00:00:00.000Z') != '')
check('carousel snap rounds to card columns',
      snap_carousel_offset(290, 280, 1000) == 280)
check('carousel snap clamps to the end',
      snap_carousel_offset(950, 280, 840) == 840)
check('image attachment decodes from url',
      decodable_attachment_url({'type': 'image', 'url': 'a', 'preview_url': 'b'})
      == 'a')
check('video attachment decodes from preview',
      decodable_attachment_url({'type': 'video', 'url': 'a', 'preview_url': 'b'})
      == 'b')
reply = {'id': '2', 'in_reply_to_id': '1',
         'account': {'id': '9', 'acct': 'stranger'}}
top = {'id': '3', 'account': {'id': '9'}}
check('reply filter keeps originals',
      filter_replies_from_non_follows([top, reply], '1', {'1': False})
      == [top])
check('reply filter keeps followed authors',
      filter_replies_from_non_follows([reply], None, {'9': True}) == [reply])
check('reply filter drops an author known not to be followed',
      filter_replies_from_non_follows([reply], None, {'9': False}) == [])

model = TimelineModel()
items = model.append_old([{'id': '2', 'content': 'b',
                           'account': {'acct': 'b'},
                           'created_at': '2026-01-01T00:00:00.000Z'},
                          {'id': '1', 'content': 'a',
                           'account': {'acct': 'a'},
                           'created_at': '2026-01-01T00:00:00.000Z'}])
check('model appends page', model.get_n_items() == 2)
check('model is a Gio.ListModel', model.get_item_type() == StatusItem.__gtype__)
check('oldest id tracked', model.oldest_id == '1')
model.insert_new([{'id': '3', 'content': 'c', 'account': {'acct': 'c'},
                   'created_at': '2026-01-01T00:00:00.000Z'}])
check('insert_new prepends', model.get_item(0).status_id == '3')
check('no duplicates', model.insert_new(
    [{'id': '3', 'content': 'c', 'account': {'acct': 'c'}}]) == 0)

# Stream handlers (delete / status.update) call these model methods; a
# missing one was an AttributeError in the live-update path.
check('remove_status drops the row',
      model.remove_status('2') and model.get_n_items() == 2)
check('remove_status is a no-op for an unknown id',
      model.remove_status('999') is False)
_before = model.get_n_items()
check('replace_status updates in place',
      model.replace_status({'id': '3', 'content': 'edited',
                            'account': {'acct': 'c'},
                            'created_at': '2026-01-01T00:00:00.000Z'})
      and model.get_n_items() == _before
      and 'edited' in model.get_item(0).status['content'])
model.get_item(0).ui['expanded'] = True
model.replace_status({'id': '3', 'content': 'again',
                      'account': {'acct': 'c'},
                      'created_at': '2026-01-01T00:00:00.000Z'})
check('replace_status keeps per-post view state',
      model.get_item(0).ui.get('expanded') is True)
check('replace_status ignores an unknown id',
      model.replace_status({'id': '999'}) is False)
check('replace_status needs an id', model.replace_status({}) is False)
model.insert_new([{'id': '7', 'account': {'acct': 'booster'},
                   'created_at': '2026-01-01T00:00:00.000Z',
                   'reblog': {'id': '3', 'content': 'x',
                              'account': {'acct': 'c'}}}])
model.remove_status('3')
check('a boost of a deleted post goes too',
      all(model.get_item(i).status_id != '3'
          for i in range(model.get_n_items())))

# A boost is the booster's own status wrapping the post that was boosted.
_boost_item = StatusItem({
    'id': '20', 'account': {'acct': 'booster', 'display_name': 'Booster'},
    'created_at': '2026-01-01T00:00:00.000Z',
    'reblog': {'id': '19', 'content': 'x',
               'account': {'acct': 'author', 'display_name': 'Author'}}})
check('a boost names the booster, not the author',
      _boost_item.boost_line == 'Booster'
      and _boost_item.display_name == 'Author')

# Home provenance: a post may reach the timeline through a followed
# hashtag rather than through a follow. The tag is matched against the
# status's own hashtag list, case-insensitively.
check('hashtag match ignores case',
      matching_tag({'tags': [{'name': 'catsofMastodon'}]},
                   ['CatsofMastodon']) == 'CatsofMastodon')
check('hashtag match needs a tag in the payload',
      matching_tag({'tags': [{'name': 'Cats'}]}, ['Dogs']) is None)
check('a boost does not carry hashtag provenance',
      matching_tag({'reblog': {'id': 'inner'},
                    'tags': [{'name': 'Cats'}]}, ['Cats']) is None)
check('no followed tags matches nothing',
      matching_tag({'tags': [{'name': 'Cats'}]}, []) is None)

_tag_model = TimelineModel()
_tag_model.group_boosts_enabled = False
_tag_model.append_old([
    {'id': 'tag1', 'content': '', 'created_at': '',
     'account': {'acct': 'stranger', 'id': 's1'},
     'tags': [{'name': 'CatsofMastodon'}]},
    {'id': 'tag2', 'content': '', 'created_at': '',
     'account': {'acct': 'friend', 'id': 'f1'}}])
_tag_items = {_tag_model.get_item(i).status_id: _tag_model.get_item(i)
              for i in range(_tag_model.get_n_items())}
_tag_changed = []
for _tag_item in _tag_items.values():
    _tag_item.connect('changed', lambda *_: _tag_changed.append(True))
_tag_hits = {'tag1': 'CatsofMastodon', 'tag2': 'CatsofMastodon'}
_tag_model.apply_source_tags(_tag_hits, {'f1': True})
check('a post from a non-followed author keeps its source tag',
      _tag_items['tag1'].ui.get('source_tag') == 'CatsofMastodon')
check('a followed author gets no source tag',
      _tag_items['tag2'].ui.get('source_tag') is None)
check('marking a source tag emits exactly one change',
      len(_tag_changed) == 1)
_tag_model.apply_source_tags(_tag_hits, {'f1': True})
check('an unchanged source tag emits nothing',
      len(_tag_changed) == 1)

_tag_items['tag1'].ui['source_tag'] = 'CatsofMastodon'
_tag_model.replace_status({'id': 'tag1', 'content': '<p>edited</p>',
                           'account': {'acct': 'stranger', 'id': 's1'}})
check('replace_status keeps the source tag',
      _tag_items['tag1'].ui.get('source_tag') == 'CatsofMastodon')

# The shell's cached height must miss when the provenance line appears:
# it adds a row above the author header.
_stamp_item = StatusItem({'id': 'stamp', 'content': '',
                          'account': {'acct': 'a', 'id': 'a1'}})
_stamp_before = _shell_stamp(StatusRun([_stamp_item]))
_stamp_item.ui['source_tag'] = 'CatsofMastodon'
check('the source tag invalidates the shell height',
      _shell_stamp(StatusRun([_stamp_item])) != _stamp_before)

# Ids are opaque strings: '99999999' sorts after '100000001' as text.
_idmodel = TimelineModel()
_idmodel.append_old([{'id': '100000001', 'account': {'acct': 'a'}, 'content': ''},
                     {'id': '99999999', 'account': {'acct': 'a'}, 'content': ''}])
check('the oldest id is chosen by value, not as text',
      _idmodel.oldest_id == '99999999')

_posts = [{'id': str(i), 'account': {'acct': 'a'}, 'content': ''}
          for i in range(9)]
_trailing = _posts + [
    {'id': 'b%d' % i, 'account': {'acct': 'booster'},
     'reblog': {'id': str(i), 'account': {'acct': 'a'}, 'content': ''}}
    for i in range(3)]
_grouped = group_boosts(_trailing)
check('three trailing boosts open a carousel',
      any(isinstance(x, BoostCarouselItem) for x in _grouped))
check('grouped boosts are not inline rows',
      sum(isinstance(x, BoostCarouselItem) for x in _grouped) == 1
      and len(_grouped) == 10)
_mostly_boosts = [{'id': 'p%d' % i, 'account': {'acct': 'a'}, 'content': ''}
                  for i in range(4)]
_mostly_boosts += [
    {'id': 'b%d' % i, 'account': {'acct': 'booster'},
     'reblog': {'id': 'p%d' % (i % 4), 'account': {'acct': 'a'},
                'content': ''}}
    for i in range(20)]
check('a boost-heavy page slots the carousel last',
      isinstance(group_boosts(_mostly_boosts)[-1], BoostCarouselItem))
_sparse = [{'id': str(i), 'account': {'acct': 'a'}, 'content': ''}
           for i in range(10)]
_sparse.append({'id': 'b0', 'account': {'acct': 'booster'},
                'reblog': {'id': '0', 'account': {'acct': 'a'},
                           'content': ''}})
check('a lone boost on a page stays inline', group_boosts(_sparse) is _sparse)
_group_model = TimelineModel()
_group_model.group_boosts_enabled = True
_group_model.append_old(_trailing)
check('model inserts a carousel row',
      any(isinstance(_group_model.get_item(i), BoostCarouselItem)
          for i in range(_group_model.get_n_items())))
check('model does not duplicate boosts inline',
      sum(1 for i in range(_group_model.get_n_items())
          if _group_model.get_item(i).is_boost) == 0)


def _post(i):
    return {'id': '%05d' % i, 'content': '', 'created_at': '',
            'account': {'acct': 'a%d' % i, 'id': str(i)}}


def _boost(i, of):
    return dict(_post(i), account={'acct': 'booster', 'id': 'b'},
                reblog=_post(of))


_page = [_post(100 - i) for i in range(6)] \
    + [_boost(50 + i, 900 + i) for i in range(6)]

# Trimming a page that held a carousel must leave its boosts fetchable.
_trim_model = TimelineModel()
_trim_model.append_old(_page)
_trim_model.append_old([_post(10 - i) for i in range(3)])
_trim_model.trim(2)
_trim_model.append_old(_page)


def _shown_ids(model):
    ids = set()
    for i in range(model.get_n_items()):
        item = model.get_item(i)
        if isinstance(item, BoostCarouselItem):
            ids |= {s['id'] for s in item.boosts}
        else:
            ids.add(item.status_id)
    return ids


check('the boosts of a trimmed page come back when it is fetched again',
      {'%05d' % (900 + i) for i in range(6)} <= _shown_ids(_trim_model))

# An unboost deletes the wrapper, not the boosted post.
_del_model = TimelineModel()
_del_model.append_old(_page)
check('deleting a boost wrapper drops its card from the carousel',
      _del_model.remove_status('00050')
      and all('00900' != s['id'] for i in range(_del_model.get_n_items())
              if isinstance(_del_model.get_item(i), BoostCarouselItem)
              for s in _del_model.get_item(i).boosts))
_other = TimelineModel()
_other.append_old(_page)
check('deleting a post that is not in the carousel changes nothing',
      not _other.remove_status('12345'))

# The same boost fetched again is not a duplicate of itself.
_dedupe = TimelineModel()
_dedupe.group_boosts_enabled = False
_dedupe.append_old([_boost(50, 900)])
_dedupe._seen.clear()
check('a boost already recorded is accepted again from the same wrapper',
      len(_dedupe._filter_unseen([_boost(50, 900)])) == 1)
check('a second boost of the same post is dropped',
      len(_dedupe._filter_unseen([_boost(51, 900)])) == 0)

# A page with nothing new still moves the paging cursor.
_paging = TimelineModel()
_paging.group_boosts_enabled = False
_paging.append_old([_boost(50, 900)])
check('an all-duplicate page returns True and moves the cursor back',
      _paging.append_old([_boost(40, 900)]) and _paging.oldest_id == '00040')
check('a page whose cursor did not move reports no more pages',
      not _paging.append_old([_boost(45, 900)]))

# Two boosts of one post on a single page dedupe too, newest first.
_same_page = TimelineModel()
_same_page.group_boosts_enabled = False
_same_page.append_old([_boost(60, 900), _boost(55, 900), _post(50)])
check('only the first boost of a post on one page is kept',
      sorted(_same_page.get_item(n).status_id
             for n in range(_same_page.get_n_items()))
      == ['00050', '00900'])
_no_dedupe = TimelineModel()
_no_dedupe.group_boosts_enabled = False
_no_dedupe.dedupe_boosts_enabled = False
_no_dedupe.append_old([_boost(60, 900), _boost(55, 900)])
check('dedupe can be switched off', _no_dedupe.get_n_items() == 2)

# The new-posts pill counts posts, not rows: a carousel holds many.
_count = TimelineModel()
check('a carousel counts as every status it replaced',
      _count.insert_new(_page) == len(_page))

# --- Inline emoji gap fixtures (unit level, no network) ----------------
# Post bodies now render :shortcode: custom emojis declared in the
# status payload as inline images; undeclared shortcodes stay literal.

EMOJI_BODY = '<p>Riding :ride4: past the :python: and the :gopher:</p>'
EMOJI_URL_BASE = 'https://emojis.example.org/custom/'

fixture_status = {
    'id': '41',
    'content': EMOJI_BODY,
    'account': {'acct': 'dash'},
    'created_at': '2026-10-01T00:00:00.000Z',
    'emojis': [{'shortcode': 'ride4', 'static_url': EMOJI_URL_BASE + 'ride4.png'},
               {'shortcode': 'python', 'static_url': EMOJI_URL_BASE + 'snake.png'},
               {'shortcode': 'gopher', 'static_url': EMOJI_URL_BASE + 'gopher.png'}],
}
bank = status_emoji_bank(fixture_status)
check('bank merges declared emojis', bank['ride4'] == EMOJI_URL_BASE + 'ride4.png')
check('bank keeps dash-prefixed verbatim',
      status_emoji_bank({
          'account': {'acct': 'dash',
                      'emojis': [{'shortcode': 'dash-gopher',
                                  'static_url': EMOJI_URL_BASE + 'dg.png'}]},
          'emojis': []})['dash-gopher'] == EMOJI_URL_BASE + 'dg.png')

body = InlineEmojiText(shorten_links=True, paragraph_gap=True)
body.set_segments(parse_content(EMOJI_BODY), bank)
def _images_of(widget, out):
    child = widget.get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Image):
            out.append(child)
        _images_of(child, out)
        child = child.get_next_sibling()
    return out

anchor_images = _images_of(body, [])
buffer_text = body.get_buffer().get_text(
    body.get_buffer().get_start_iter(), body.get_buffer().get_end_iter(),
    True)
check('declared shortcodes render as images',
      len(anchor_images) == 3)
check('labeled shortcodes leave the buffer',
      ':ride4:' not in buffer_text and ':gopher:' not in buffer_text)
check('a11y label keeps raw shortcodes',
      ':ride4:' in body._raw and body._raw is not None)

missing = InlineEmojiText()
missing.set_segments(parse_content(EMOJI_BODY), {})
missing_text = missing.get_buffer().get_text(
    missing.get_buffer().get_start_iter(),
    missing.get_buffer().get_end_iter(), True)
check('undeclared shortcodes stay literal',
      ':ride4:' in missing_text and ':python:' in missing_text)
check('undeclared shortcodes draw no images',
      len(_images_of(missing, [])) == 0)

# Clicking a link must resolve the href: Gtk.TextView.get_iter_at_location
# returns (found, iter), so unpacking it wrongly raised AttributeError and
# links only worked from the keyboard.
link_body = InlineEmojiText()
link_body.set_segments([{'text': 'see ', 'href': None},
                        {'text': 'example.com',
                         'href': 'https://example.com/x'},
                        {'text': ' now', 'href': None}])
check('link segment registers a hit range',
      link_body._href_at_offset(6) == 'https://example.com/x')
check('plain text resolves to no link',
      link_body._href_at_offset(0) is None)
link_window = Gtk.Window()
link_window.set_child(link_body)
link_window.set_default_size(400, 100)
link_window.present()
for _ in range(60):
    while GLib.main_context_default().pending():
        GLib.main_context_default().iteration(False)
_iter = link_body.get_buffer().get_iter_at_offset(6)
_rect = link_body.get_iter_location(_iter)
check('a click on the link text resolves the href',
      link_body._href_at(_rect.x + 2, _rect.y + 2)
      == 'https://example.com/x')

# Leaving a link restores the body's text cursor. It was cached at
# construction, where the input-module swap leaves GTK's cursor as the
# invisible "none" it uses while typing, so the pointer vanished over
# any body that had a link to hover.
check('a mapped body caches the text cursor, not none',
      link_body._text_cursor is not None
      and link_body._text_cursor.get_name() == 'text')
link_body._set_over_link(True)
check('a link hovers with the pointer cursor',
      link_body.get_cursor().get_name() == 'pointer')
link_body._set_over_link(False)
check('leaving a link brings the text cursor back',
      link_body.get_cursor().get_name() == 'text')
link_window.set_child(None)

# Fixture-render probe: a bound StatusRow (the same path used by new,
# paginated and thread posts) must show inline Gtk.Images in its body.
Adw.init()
Gtk.init()

class _DummyClient:
    token = None

row = StatusRow()
row.bind(StatusItem(fixture_status), _DummyClient())

def collect_emojis(widget, out):
    if isinstance(widget, Gtk.Image) and widget.get_pixel_size() == 20:
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect_emojis(child, out)
        child = child.get_next_sibling()
    return out

emoji_window = Gtk.Window()
emoji_window.set_child(row)
emoji_window.set_default_size(1400, 950)
emoji_window.present()

deadline = time_module.monotonic() + 5.0
while time_module.monotonic() < deadline:
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass
in_row = collect_emojis(row, [])
check('bound row inlines emoji images', len(in_row) == 3)
row_buffer = row.content.get_buffer().get_text(
    row.content.get_buffer().get_start_iter(),
    row.content.get_buffer().get_end_iter(), True)
check('bound row body has no literal declared shortcodes',
      ':ride4:' not in row_buffer and ':python:' not in row_buffer)

poll_status = {**fixture_status, 'poll': {
    'id': '7',
    'options': [{'title': 'Vote :ride4:',
                 'votes_count': 2},
                {'title': 'Abstain', 'votes_count': 1}],
    'votes_count': 3, 'expired': True, 'closed': True, 'own_votes': []}}
poll_row = StatusRow()
poll_row.bind(StatusItem(poll_status), _DummyClient())

def collect_poll_emojis(widget, out):
    if isinstance(widget, Gtk.Image) and widget.get_pixel_size() == 15:
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect_poll_emojis(child, out)
        child = child.get_next_sibling()
    return out

check('poll option titles inline declared emoji',
      len(collect_poll_emojis(poll_row, [])) == 1)

# A poll declares its own emoji for the option titles; that array is
# honoured even when the post's own payload never mentions the code.
own_poll_status = {**fixture_status, 'poll': {
    'id': '8',
    'options': [{'title': 'Vote :newt:', 'votes_count': 2}],
    'emojis': [{'shortcode': 'newt',
                'static_url': EMOJI_URL_BASE + 'newt.png'}],
    'votes_count': 2, 'expired': True, 'closed': True, 'own_votes': []}}
# Short handles: the instance part is dropped on the timeline because
# the tooltip and the accessible label still carry it; a short handle
# that only repeats the display name is dropped with it.
def bind_with_account(account):
    row = StatusRow()
    row.bind(StatusItem({'id': '9', 'content': '<p>x</p>',
                         'account': account,
                         'created_at': '2026-10-01T00:00:00.000Z'}),
             _DummyClient())
    return row

local_row = bind_with_account({'acct': 'dash', 'display_name': 'Dasher'})
check('local handle short', local_row.handle_label.get_text() == '@dash'
      and local_row.handle_label.get_visible())
repeat_row = bind_with_account({'acct': 'dash', 'display_name': 'Dash'})
check('repeating handle suppressed',
      not repeat_row.handle_label.get_visible())
remote_row = bind_with_account({'acct': 'alice@other.social'})
check('remote handle keeps only the user',
      remote_row.handle_label.get_text() == '@alice')
check('full handle stays in the tooltip',
      remote_row.handle_label.get_tooltip_text() == '@alice@other.social')

# Home provenance line: a post pulled in by a followed hashtag names the
# tag above the author header, while a boost keeps its own boost line.
def collect_labels(widget, out):
    if isinstance(widget, Gtk.Label):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        collect_labels(child, out)
        child = child.get_next_sibling()
    return out


def tag_status(status_id, **extra):
    return {'id': status_id, 'content': '<p>a photo of a cat</p>',
            'account': {'acct': 'catposter', 'id': 'c1'},
            'created_at': '2026-10-01T00:00:00.000Z', **extra}


tag_item = StatusItem(tag_status('tagpost',
                                 tags=[{'name': 'CatsofMastodon'}]))
tag_item.ui['source_tag'] = 'CatsofMastodon'
tag_row = StatusRow()
tag_row.bind(tag_item, _DummyClient())
check('the source hashtag line shows',
      tag_row.source_line is not None and tag_row.source_line.get_visible())
check('the source hashtag line names the tag',
      any(label.get_text() == 'CatsofMastodon'
          for label in collect_labels(tag_row, [])))

plain_row = StatusRow()
plain_row.bind(StatusItem(tag_status('plainpost')), _DummyClient())
check('a post without provenance shows no source line',
      plain_row.source_line is None
      or not plain_row.source_line.get_visible())

boost_row = StatusRow()
boost_row.bind(StatusItem({
    'id': 'boostpost',
    'account': {'acct': 'booster', 'id': 'b1', 'display_name': 'Booster'},
    'created_at': '2026-10-01T00:00:00.000Z',
    'reblog': tag_status('innerpost', tags=[{'name': 'CatsofMastodon'}])}),
    _DummyClient())
check('a boost keeps a visible source line',
      boost_row.source_line is not None
      and boost_row.source_line.get_visible())
check('a boost does not claim hashtag provenance',
      not any(label.get_text() == 'CatsofMastodon'
              for label in collect_labels(boost_row, [])))

# Only the first post of a run carries the line: the card prints the
# header (and the provenance) once.
tag_row.set_run(2, 2)
check('a later post of a run hides the source line',
      not tag_row.source_line.get_visible())
tag_row.set_run(1, 2)
check('the first post of a run shows the source line again',
      tag_row.source_line.get_visible())

# Action bar: on timeline cards it is hidden by default (the actions
# live in the post's menu) and always up on detail views.
bar_row = bind_with_account({'acct': 'patrol'})
check('action bar hidden on patrol cards', not bar_row.actions.get_visible())
check('timeline row actions and menu bind through the shared adapter',
      isinstance(bar_row.actions, StatusActions)
      and isinstance(bar_row._overflow, StatusMenuButton)
      and bar_row.actions.item is bar_row.item
      and bar_row.actions._client is bar_row.client
      and bar_row._overflow.item is bar_row.item
      and bar_row._overflow.actions is bar_row.actions)
check('detail rows keep the action bar',
      StatusRow(detail=True).actions.get_visible())

# A 'blur' filter covers just the media: the badge stays, the text
# stays, and Show Anyway clears both the badge and the blur.
def media_status(order, title='Spoiler'):
    status = {'id': '9', 'content': '<p>text for the filtered card</p>',
              'account': {'acct': 'patrol'},
              'created_at': '2026-10-01T00:00:00.000Z',
              'media_attachments': [{
                  'type': 'image', 'url': EMOJI_URL_BASE + 'x.png'}]}
    if order is not None:
        status['filtered'] = [{
            'filter': {'filter_action': order, 'title': title}}]
    return status

def deep_pictures(widget, out):
    if isinstance(widget, BlurredPicture):
        out.append(widget)
    child = widget.get_first_child()
    while child is not None:
        deep_pictures(child, out)
        child = child.get_next_sibling()

blur_row = StatusRow()
blur_item = StatusItem(media_status('blur'))
blur_row.bind(blur_item, _DummyClient())
check('blur shows the badge', blur_row.filter_bar.get_visible())
check('blur does not collapse the post', blur_row.main.get_visible())
check('blur badge names the filter',
      blur_row.filter_label.get_text() == 'Filtered: Spoiler')
blurred_seen = []
deep_pictures(blur_row, blurred_seen)
check('blur wraps the media picture', len(blurred_seen) == 1)

normal_row = StatusRow()
normal_row.bind(StatusItem(media_status(None)), _DummyClient())
plain_seen = []
deep_pictures(normal_row, plain_seen)
check('unfiltered media is not blurred', not plain_seen)

blur_item.ui['filter_shown'] = True
blur_row.bind(blur_item, _DummyClient())
check('Show Anyway clears badge and blur',
      not blur_row.filter_bar.get_visible())
unblurred_seen = []
deep_pictures(blur_row, unblurred_seen)
check('media unblurred after Show Anyway', not unblurred_seen)

# 'warn' still collapses the whole card until shown.
warn_row = StatusRow()
warn_item = StatusItem(media_status('warn'))
warn_row.bind(warn_item, _DummyClient())
check('warn collapses the card', not warn_row.main.get_visible())
check('warn badge names the filter',
      warn_row.filter_label.get_text() == 'Filtered: Spoiler')
for order in ('warn', 'blur'):
    untitled_row = StatusRow()
    untitled_row.bind(StatusItem(media_status(order, title='')),
                      _DummyClient())
    check('%s without a title says only Filtered' % order,
          untitled_row.filter_label.get_text() == 'Filtered')

emoji_window.set_child(None)

# Live rendering check
Adw.init()
Gtk.init()

client = MastodonClient('https://fosstodon.org')
page = TimelinePage(client, mode='public')
window = Gtk.Window()
window.set_child(page)
window.set_default_size(600, 900)
window.present()

loop = GLib.MainLoop()
result = {'done': False}


def on_timeout():
    result['done'] = True
    loop.quit()


timeout_id = GLib.timeout_add_seconds(45, on_timeout)


def poll():
    if result['done']:
        return False
    if page.model.get_n_items() > 0 and page.stack.get_visible_child_name() == 'list':
        loop.quit()
    return True


GLib.timeout_add(200, poll)
loop.run()
if not result['done']:
    GLib.source_remove(timeout_id)

if page.stack.get_visible_child_name() != 'list':
    print('SKIP - public timeline unreachable (network error)')
    sys.exit(77)

check('real statuses loaded', page.model.get_n_items() > 0)
item = next(page.model.get_item(i) for i in range(page.model.get_n_items())
            if not isinstance(page.model.get_item(i), BoostCarouselItem))
check('status has author', bool(item.handle))

# Give the list view a moment to map and bind visible rows.
import time as time_module

deadline = time_module.monotonic() + 5.0
while time_module.monotonic() < deadline:
    ctx = GLib.MainContext.default()
    while ctx.iteration(False):
        pass
    if page.list_view.get_first_child() is not None:
        inner = page.list_view.get_first_child()
        if inner.get_first_child() is not None:
            break
row_widgets = []

def collect(widget):
    row_widgets.append(widget)
    if isinstance(widget, Gtk.Widget):
        child = widget.get_first_child()
        while child is not None:
            collect(child)
            child = child.get_next_sibling()

collect(page.list_view)
check('avatars rendered', any(isinstance(w, Adw.Avatar) for w in row_widgets))
check('content labels rendered',
      any(isinstance(w, Gtk.Label) for w in row_widgets))
check('post cards rendered',
      any(isinstance(w, StatusRow) for w in row_widgets))
pictures = [w for w in row_widgets if isinstance(w, Gtk.Picture)]
print('note - %d GtkPicture media widgets instantiated' % len(pictures))

# The column follows the window: at every width it is min(viewport, cap),
# centered, and never wider than the window. The cap is COLUMN_WIDTH in
# the clamp's unit, which libadwaita defaults to sp, so it scales with
# the text-scaling factor: at 0.9 that is 660 * 86/96 = 591.25px, not
# 660 * 0.9 -- the factor is applied as a DPI, and DPI is whole.
# A large one-step shrink
# (1270 -> 500) used to leave margin-inflated minimums behind and push
# the cards off the right edge.
def settle(seconds):
    end = time_module.monotonic() + seconds
    while time_module.monotonic() < end:
        ctx = GLib.MainContext.default()
        while ctx.iteration(False):
            pass


for width in (1270, 500, 1270, 800):
    window.set_default_size(width, 900)
    settle(1.5)
    scroller = page.list_view.get_parent().get_parent()
    ok_s, outer = scroller.compute_bounds(window)
    ok_l, inner = page.list_view.compute_bounds(scroller)
    if not (ok_s and ok_l):
        check('column measurable at %d' % width, False)
        continue
    viewport = outer.size.width
    clamp = page.list_view.get_parent()
    cap = Adw.LengthUnit.to_px(clamp.get_unit(), COLUMN_WIDTH,
                               clamp.get_settings())
    left = inner.origin.x
    right = viewport - inner.origin.x - inner.size.width
    print('note - window %d: viewport %d, column %d at %d, cap %.1f' % (
        window.get_width(), viewport, inner.size.width, left, cap))
    check('viewport fits the window at %d' % width,
          outer.origin.x + viewport <= window.get_width() + 0.5)
    check('column is min(viewport, %.1f) at %d' % (cap, width),
          abs(inner.size.width - min(viewport, cap)) <= 1)
    check('column centered at %d' % width, abs(left - right) <= 1)

if failures:
    print('%d checks failed' % len(failures))
    sys.exit(1)
print('all timeline rendering checks passed')
