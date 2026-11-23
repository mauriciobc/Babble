# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit what Feed Chef chose, offline, from the logs it keeps.

Every page the engine cooks writes a schema-2 audit record beside its
decision log (``~/.local/state/babble/feed_chef_logs``, a ``0700`` folder): the
recipe as it was, the posts with where each came from, the clock, the
trends, the follow verdicts and every stage's verdict on every post, with
the visible text in a private ``*.text.json`` sidecar. Nothing here is
installed; it runs from the source tree and reads only those files.

* ``show LOG [--post ID] [--text]`` reads one log: the recipe and its hash,
  the clock, how many posts each reason removed, the posts it kept in order
  with their score breakdown, and why each other post went. No text unless
  ``--text``.
* ``replay LOG...`` cooks the recorded page again through the real engine
  (the same inputs, the recorded clock, the recorded verdicts of what is
  not replayable: the viewer's own filters, the sentiment engine) and
  compares ids, order, reasons and scores bit for bit. Exit 0 identical,
  1 a mismatch (naming the first differing stage and post), 2 a record that
  cannot be replayed (another engine revision, unless ``--force``).
* ``evaluate LOG...`` judges the output against the recipe it recorded:
  each kept post keeps every promise, the order is the recipe's, heat
  removed exactly floor(P x n / 100), the cap per person holds across the
  pages of a cook, the hashtags and follow setting are respected, and every
  removal is justified. Each rule is PASS or FAIL, with no tolerance.
* ``collect [--recipe ID] [--since DATE] [--json PATH]`` replays and
  evaluates a folder of logs and prints a report per recipe and cook.
  ``--json`` writes counts, verdicts, the recipe hash and dates only: no
  post id, handle or text.
* ``langbench [LOG...] [--simplemma]`` scores language detection against the
  languages posts declare: precision, recall and coverage by threshold and
  text length (numbers only; needs numpy and py3langid).
* ``self-test`` checks all of that on synthetic cooks, tampered records
  included, with no network and no display
  (``meson test -C build 'feedchef audit'``).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (HERE, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ.setdefault('GSETTINGS_BACKEND', 'memory')

from feedchefaudit.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
