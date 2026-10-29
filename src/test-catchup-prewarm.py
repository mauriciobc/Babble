# SPDX-License-Identifier: GPL-3.0-or-later

"""The tokenizer patterns are built once, whoever asks first and however many.

The first `rank()` of a process walks every Unicode code point to build its
regexes (about 110 ms). The Catch Up page pre-warms that on a worker thread,
and the snapshot worker it starts beside it ranks too, so two threads can ask
for the patterns together. `functools.lru_cache` would let both run the scan;
this pins that the build is single-flight. No display, no wall-clock limit:
the checks count builds. Each case runs in a fresh interpreter, because the
only honest state is a cold one.
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


def fresh(body):
    """Run *body* in a cold interpreter; its stdout, or None on failure."""
    code = ('import sys, threading, time; sys.path.insert(0, %r)\n'
            'from src.catchup import rank_text\n%s' % (root, body))
    run = subprocess.run([sys.executable, '-c', code],
                         capture_output=True, text=True, env=os.environ)
    if run.returncode != 0:
        print(run.stderr)
        return None
    return run.stdout.strip()


out = fresh('''
assert rank_text._PATTERNS is None, "already warm"
rank_text.prewarm()
assert rank_text._PATTERNS is not None, "prewarm did not build"
print("ok")
''')
check('prewarm() builds the patterns', out == 'ok')

# Two threads, one ten milliseconds behind: each build is made slow so the
# second thread certainly arrives while the first is still scanning.
out = fresh('''
builds = []
real = rank_text._category_ranges
def slow(prefixes):
    builds.append(threading.current_thread().name)
    time.sleep(0.15)
    return real(prefixes)
rank_text._category_ranges = slow
seen = {}
def ask(name, delay):
    time.sleep(delay)
    seen[name] = rank_text._patterns()
threads = [threading.Thread(target=ask, args=("prewarm", 0), name="prewarm"),
           threading.Thread(target=ask, args=("rank", 0.01), name="rank")]
for t in threads: t.start()
for t in threads: t.join()
print(len(builds), seen["prewarm"] is seen["rank"])
''')
check('two concurrent callers run the scan once and share the result',
      out == '1 True')

out = fresh('''
first = rank_text._patterns()
real = rank_text._category_ranges
rank_text._category_ranges = lambda prefixes: 1 / 0
print(rank_text._patterns() is first)
''')
check('a built set is returned without taking the build path again',
      out == 'True')

if failures:
    raise SystemExit('%d prewarm checks failed' % len(failures))
print('all prewarm checks passed')
