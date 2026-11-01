# SPDX-License-Identifier: GPL-3.0-or-later
"""Unit tests for thread chain detection (no display, no network)."""

import os
import sys

os.environ['GSETTINGS_BACKEND'] = 'memory'

srcdir = os.environ.get('BABBLE_SRCDIR', os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.threadchain import own_thread, plain_text, preview_tail  # noqa: E402

failed = False


def check(name, cond):
    global failed
    print(('ok - ' if cond else 'FAIL - ') + name)
    if not cond:
        failed = True


ME, OTHER = '10', '20'


def post(pid, parent=None, who=ME, day=None):
    return {'id': str(pid), 'in_reply_to_id': parent,
            'account': {'id': who},
            'created_at': '2026-01-01T00:%02d:00Z' % (day or int(pid) % 60)}


chain = [post(1), post(2, '1'), post(3, '2'), post(4, '3'), post(5, '4')]


def at(n, extra_desc=(), extra_anc=()):
    return own_thread(chain[n - 1], list(extra_anc) + chain[:n - 1],
                      chain[n:] + list(extra_desc), ME)


pos = at(3)
check('middle of 5: index 3 total 5 previous=post2',
      pos and (pos.index, pos.total, pos.previous['id']) == (3, 5, '2'))
pos = own_thread(chain[2], chain[:2], chain[3:], ME)
check('middle index/total', (pos.index, pos.total) == (3, 5))
pos = at(1)
check('first: index 1, previous None',
      pos and (pos.index, pos.total, pos.previous) == (1, 5, None))
pos = at(5)
check('last: index 5 total 5 previous=post4',
      pos and (pos.index, pos.previous['id']) == (5, '4'))

other = post(90, '3', OTHER)
check('reply by another account does not extend',
      at(3, [other]).total == 5)
tail_other = post(91, '5', OTHER)
check('other account reply after the tail does not extend',
      at(1, [tail_other]).total == 5)
own_elsewhere = post(92, '77', ME)
check('own reply to someone else\'s post is ignored',
      at(1, [own_elsewhere]).total == 5)
check('own reply to other\'s reply is ignored',
      at(1, [other, post(93, '90', ME)]).total == 5)

first = post(1)
late = post(5, '1', day=30)
early = post(3, '1', day=10)
pos = own_thread(first, [], [late, early, post(4, '3', day=11)], ME)
check('two own replies: oldest continues',
      pos.total == 3 and pos.index == 1)
pos = own_thread(early, [first], [post(4, '3', day=11)], ME)
check('oldest branch counts, position of its member', pos.index == 2)

check('non-own status -> None',
      own_thread(post(50, who=OTHER), [], [post(51, '50', OTHER)], ME)
      is None)
check('single own post -> None', own_thread(first, [], [], ME) is None)
check('no account id -> None', own_thread(first, [], [post(2, '1')], None)
      is None)

anc = [post(1), post(2, '1', OTHER), post(3, '2'), post(4, '3')]
pos = own_thread(post(5, '4'), anc, [], ME)
check('walk up stops at another author',
      pos and (pos.index, pos.total, pos.previous['id']) == (3, 3, '4'))
anc2 = [post(1), post(2, '1', OTHER)]
check('only other-author parent -> None',
      own_thread(post(3, '2'), anc2, [], ME) is None)
check('int ids compared as strings',
      own_thread({'id': 2, 'in_reply_to_id': 1, 'account': {'id': 10}},
                 [{'id': 1, 'in_reply_to_id': None, 'account': {'id': 10}}],
                 [], '10').total == 2)

check('preview: short text unchanged, no ellipsis',
      preview_tail('Hello there') == 'Hello there')
check('preview: empty', preview_tail('') == '' and preview_tail(' \n ') == '')
long = ' '.join('word%03d' % i for i in range(60))
out = preview_tail(long)
check('preview: prefixed and shortened',
      out.startswith('…') and len(out) <= 141 and out.endswith('word059'))
check('preview: cut at a word boundary', out[1:].split()[0].startswith('word')
      and len(out[1:].split()[0]) == 7)
check('preview: newlines collapsed', preview_tail('a\n\nb') == 'a b')
check('preview: exactly limit unchanged',
      preview_tail('x' * 140) == 'x' * 140)
check('preview: limit param', preview_tail('aaa bbb ccc ddd', 7) == '…ccc ddd')

check('plain: strips html', plain_text(
    {'content': '<p>Hello <a href="x">world</a>!</p>'}) == 'Hello world!')
text = plain_text({'content': '<p>One</p><p>Two<br>three</p>'})
check('plain: paragraph and line breaks', text == 'One\n\nTwo\nthree')
check('plain: empty', plain_text({}) == '' and plain_text(
    {'content': None}) == '')
check('plain: entities', plain_text({'content': '<p>a &amp; b</p>'}) == 'a & b')

sys.exit(1 if failed else 0)
