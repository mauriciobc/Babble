# SPDX-License-Identifier: GPL-3.0-or-later

"""What the app pays at start-up for Catch Up it may never open.

`accounts` is imported by every window, and it deletes a Catch Up snapshot
store when an account is removed. Importing a submodule runs the package
`__init__`, so an eager import there loads the whole pipeline (and with it
`digest` and `digest_layout`) for every reader. Each case runs in a fresh
interpreter, because the only honest answer is what `sys.modules` holds
after the import.
"""

import os
import subprocess
import sys

srcdir = os.environ.get('BABBLE_SRCDIR') \
    or os.path.dirname(os.path.abspath(__file__))
root = os.path.dirname(os.path.abspath(srcdir))

failures = []


def check(label, condition):
    if condition:
        print('ok - %s' % label)
    else:
        failures.append(label)
        print('FAIL - %s' % label)


def loaded_after(statement):
    """The `src.catchup` modules loaded by *statement* in a fresh process."""
    code = ('import sys; sys.path.insert(0, %r); %s; '
            'print(" ".join(sorted(m[len("src.catchup."):] '
            'for m in sys.modules if m.startswith("src.catchup."))))'
            % (root, statement))
    run = subprocess.run([sys.executable, '-c', code],
                         capture_output=True, text=True, env=os.environ)
    if run.returncode != 0:
        print(run.stderr)
        return None
    return set(run.stdout.split())


# Everything beyond the snapshot store: the pipeline and its data layer,
# the ranker, and the widgets.
HEAVY = {'pipeline', 'digest', 'digest_layout', 'rank', 'rank_model',
         'rank_text', 'fetch', 'rows', 'page', 'page_controls', 'tiles',
         'tiles_render', 'geometry'}

for label, statement in (
        ('importing the snapshot store', 'from src.catchup import store'),
        ('importing accounts, which every window loads',
         'import src.accounts')):
    modules = loaded_after(statement)
    check('%s runs' % label, modules is not None)
    if modules is not None:
        extra = sorted(modules & HEAVY)
        check('%s loads no pipeline, ranker or widget (%s)'
              % (label, ', '.join(extra) or 'none'), not extra)

modules = loaded_after('import src.catchup as c; c.run')
check('`catchup.run` still resolves, loading the pipeline on demand',
      modules is not None and 'pipeline' in modules)

if failures:
    raise SystemExit('%d catchup import checks failed' % len(failures))
print('all catchup import checks passed')
