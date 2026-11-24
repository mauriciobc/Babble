# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional third-party dependencies of Feed Chef, imported on demand.

Nothing here imports a dependency at load time, and nothing here touches
GTK: the first `get(name)` imports the module with `importlib`, and the
result (or the reason it failed) is cached. Importing numpy and friends
takes time and memory, so the call belongs on a worker thread; a call on
the main thread logs a warning, once per name, unless the caller says it is
deliberate (`allow_main=True`), so a later misuse is visible in the log.

The lock guards only the caches, never the import itself: two threads may
import the same module at once (Python's own import lock serialises that),
and the first result stored wins.
"""

import importlib
import importlib.util
import logging
import threading

log = logging.getLogger(__name__)

NAMES = ('numpy', 'py3langid', 'kmedoids', 'segno')

_lock = threading.Lock()
_modules = {}
_reasons = {}
_warned = set()


def get(name, allow_main=False):
    """The module called *name*, or None when it cannot be imported (or is
    not one of :data:`NAMES`; that answer is not cached)."""
    if name not in NAMES:
        return None
    if (not allow_main
            and threading.current_thread() is threading.main_thread()):
        with _lock:
            first = name not in _warned
            _warned.add(name)
        if first:
            log.warning('feedchef.deps.get(%r) called on the main thread; '
                        'importing it can stall the interface', name)
    with _lock:
        if name in _modules:
            return _modules[name]
    try:
        module = importlib.import_module(name)
        failure = ''
    except Exception as error:  # ImportError, or a broken build
        module = None
        failure = '%s: %s' % (type(error).__name__, error)
        log.info('feedchef dependency %s unavailable: %s', name, failure)
    with _lock:
        if module is None:
            _reasons.setdefault(name, failure)
        return _modules.setdefault(name, module)


def available(name, allow_main=False):
    """Whether *name* imports."""
    return get(name, allow_main=allow_main) is not None


def findable(name):
    """Whether *name* is installed, found without importing it (so it is
    cheap and safe on the main thread, which the builder asks from)."""
    if name not in NAMES:
        return False
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def unavailable_reason(*names):
    """Why the first of *names* that cannot be used cannot, for the
    interface, or '' when all of them can be: an import that already
    failed says so, otherwise a module that is not installed. Never
    imports anything."""
    for name in names:
        failure = _reasons.get(name)
        if failure:
            return failure
        if name in _modules and _modules[name] is not None:
            continue
        if not findable(name):
            return '%s is not installed' % name
    return ''


def reason(name):
    """Why *name* is unavailable, for the interface; '' when it is there
    (or has not been asked for yet, or is not a known name)."""
    return _reasons.get(name, '')
