# SPDX-License-Identifier: GPL-3.0-or-later
"""The ranked timeline's evaluation harness.

`collect` records the reader's own feed on the reader's own machine, so the
ranker can later be judged on real posts without any of them leaving it.
Nothing here is installed; it runs from the source tree.

One run reads the instance through Babble's own
`src.mastodon.MastodonClient` -- the same client, and so the same JSON, the
app ranks -- and appends:

* the whole home timeline, `limit=40`, paged with the `Link` header's next
  parameters until a page is empty or 20 pages are done (Mastodon keeps at
  most 800 entries);
* the first page of the reader's own statuses (boosts and replies carry
  exact times, which the flags do not);
* the first page of favourites;
* the first page of bookmarks;
* the relationships of the authors, boosters and reply targets not stored
  yet, 40 ids per request.

The token comes from one of two places:

* ``BABBLE_EVAL_TOKEN``, when it is set;
* otherwise, with ``--account-id ID``, from the keyring entry Babble wrote
  for that account, read with ``keyringstore.load_secret_async(instance, ID,
  callback)``. The routine runs on the main loop, so a `GLib.MainLoop`
  runs until it answers. That route only finds entries an uninstalled
  (host) Babble wrote: a Flatpak install keeps its secrets inside the
  sandbox, where this script cannot reach them. A volunteer using the
  Flatpak therefore creates a token by hand --
  Preferences -> Development -> New application, with only the ``read``
  scope -- and puts it in ``BABBLE_EVAL_TOKEN``.

Neither a post's text nor the token is ever printed. A run prints one line::

    <ts> posts=<n> new=<n> requests=<n> partial=<bool>

The data lives in
``${XDG_CACHE_HOME:-~/.cache}/babble-eval/i-<quoted instance>/eval.sqlite3``:
a ``0700`` directory holding a ``0600`` file, one snapshot of every post's
counters and flags per run. The ``i-`` prefix keeps ``.`` and ``..`` out of
the path, since ``urllib.parse.quote`` leaves ``.`` alone.

`token`, `follow`, `engage` and `inspect` are the harness's half of the
managed accounts (D2). The maintainer creates three accounts by hand and
hands over their tokens; `token` verifies one against
``/accounts/verify_credentials`` and stores it in the keyring under the id
the server names, where ``collect --account-id`` reads it. It never prints
the token, on the success path or the failure path. `follow` follows
accounts for one managed account and reports the resulting follow count:
the candidates come from another account's following list or from ids
given on the command line, accounts already followed are skipped, and
``--limit`` plus ``--pause`` build an account over several runs without
tripping the rate limit. `engage` produces one of the four engagements
T4.2 credits -- ``favourite``, ``reblog``, ``bookmark``, ``reply`` -- and
`inspect` reports, per account, its follow counts and how many home
timeline posts it can see, which is the first thing to check before the
14-day run. All four fail with a line naming what was refused, never a
traceback.

`evaluate` reads that database back and judges it, one local day at a time
at 06:00 local (D3). A day is judged only when its 48-hour window was
sampled at most two hours apart; the others are reported ``no-coverage``
and left out of every count. For each judged day it builds the feed the
reader had at T -- posts created in [T - 24 h, T), not engaged by then,
their counters from the last observation at or before T -- labels the
posts first seen engaged in (T, T + 24 h], and compares five orderings of
the same feed: chronological, Catch Up's ranker without and with affinity,
engagement-only, and affinity-then-chronological. It prints
recall@20, recall@50, distinct authors, small-account share and overlap
per ordering, then the four gate verdicts and the H1 record. With
``--json`` it writes the same numbers, and no post, id or handle, to a
summary another machine can share.

`aggregate` folds those summaries into the H1 record: Gate A passes when
at least ``min_accounts_passing`` accounts pass it, and the ordering and
the affinity decision are the accounts' majority, ties falling back to
``rank`` without affinity. Both commands read
``bench/rank_eval_gates.json`` unless ``--gates`` names another file. With
no gates file they print the metrics and refuse to print a verdict: the
thresholds were frozen before the data came in (H0), so they cannot be
chosen after it.

`self-test` checks all of that against a world whose answer is known, in
a temporary directory and with no network: five days of hourly runs, more
candidates a day than the top 20 holds, and labels planted on one author's
posts after each day's evaluation time. It asserts recall@k on an ordering
worked out by hand, the temporal split (and, in a second database that
differs by one row, that reading past the day *would* change the top 20),
the whole-future rewrite twice over -- once from a chosen day's
evaluation time, and once from an hour into the compared day's own
future, so a bound one hour too far cannot hide behind the boundary --
the sign test's two anchors, the gate verdicts on a fixture gates file
(each of the four on the fixture where its own condition decides), the
summary's privacy in keys and in values, candidate counters that move,
a day with a hole in its coverage, and `collect` through a duck-typed
fake client that answers with the real endpoints' shapes.
`src.mastodon.MastodonClient` is replaced by a class whose ``__init__``
raises, so a run that reached for the real client fails here: patching
`socket.socket` would prove nothing, because libsoup does not use
Python's sockets.

`bench` reproduces the cost figure in `Docs/timeline-algorithm.md` §6.1:
`src.catchup.rank.rank()` on 400 and on 800 synthetic posts, three runs each,
every run printed.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for path in (HERE, ROOT):
    if path not in sys.path:
        sys.path.insert(0, path)

from rankeval.cli import main  # noqa: E402

if __name__ == '__main__':
    sys.exit(main())
