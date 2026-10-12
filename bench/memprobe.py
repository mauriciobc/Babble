# SPDX-License-Identifier: GPL-3.0-or-later
"""Memory probe run inside a live Babble by bench/memwatch.py.

memwatch.py prepends ``OPTIONS = {...}`` to this file and hands it to
``sys.remote_exec``, so it runs on the app's main thread, where touching
GTK is safe. It appends one JSON line to ``OPTIONS['out']``:

- process: RSS, peak RSS, threads;
- live Python objects of Babble's own types, and how many of the widget
  types are not in any window's widget tree (alive but unparented);
- every non-empty dict/list/set/deque held by a Babble module or class
  (caches, queues, registries) with its length;
- Babble list models and their item counts;
- decoded textures with a wrapper, and an estimate of their pixel memory;
- glibc heap figures (mallinfo2), and with ``trim`` the RSS returned by
  ``malloc_trim(0)``: memory the allocator holds but Python freed;
- with tracemalloc running (PYTHONTRACEMALLOC), the allocation sites that
  grew most since the previous probe;
- with ``chain`` set to a type name, the referrer chain of a few live
  instances of it, ending where Python can no longer see who holds them.
"""

import collections
import ctypes
import gc
import json
import sys
import time
import traceback

_SKIP_TRACE = ('<frozen importlib._bootstrap>', '<unknown>', '*tracemalloc*',
               '*memprobe*')


def _proc_status():
    out = {}
    with open('/proc/self/status') as handle:
        for line in handle:
            key, _, value = line.partition(':')
            if key in ('VmRSS', 'VmHWM'):
                out[key] = int(value.split()[0]) // 1024
            elif key == 'Threads':
                out[key] = int(value)
    return {'rss_mb': out.get('VmRSS'), 'peak_mb': out.get('VmHWM'),
            'threads': out.get('Threads')}


def _type_name(kind):
    return '%s.%s' % (kind.__module__, kind.__qualname__)


# The app's package: ``babble`` when launched, ``src`` when the modules
# are imported from the source tree as the tests and the benchmark do.
PACKAGE = OPTIONS.get('package', 'babble')  # noqa: F821


def _in_package(module):
    return module == PACKAGE or module.startswith(PACKAGE + '.')


def _is_babble(kind):
    return _in_package(getattr(kind, '__module__', '') or '')


def _widget_tree_counts(Gtk):
    """Babble widgets reachable from a toplevel window."""
    counts = collections.Counter()
    stack = list(Gtk.Window.list_toplevels())
    while stack:
        widget = stack.pop()
        kind = type(widget)
        if _is_babble(kind):
            counts[_type_name(kind)] += 1
        child = widget.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return counts


def _containers():
    """Lengths of the containers Babble keeps at module or class level.
    ALL_CAPS names are constants (tables of icons, tags, labels) and are
    left out; caches and queues are lower-case by convention."""
    kinds = (dict, list, set, collections.deque)
    found = {}

    def wanted(attr, value):
        return (not attr.startswith('__') and not attr.isupper()
                and isinstance(value, kinds) and len(value))

    for name, module in list(sys.modules.items()):
        if module is None or not _in_package(name):
            continue
        for attr, value in list(vars(module).items()):
            if wanted(attr, value):
                found['%s.%s' % (name, attr)] = len(value)
            elif isinstance(value, type) and value.__module__ == name:
                for cattr, cvalue in list(vars(value).items()):
                    if wanted(cattr, cvalue):
                        found['%s.%s.%s' % (name, attr, cattr)] = len(cvalue)
    return found


def _mallinfo():
    try:
        libc = ctypes.CDLL('libc.so.6')
    except OSError:
        return None, None

    class Mallinfo2(ctypes.Structure):
        _fields_ = [(field, ctypes.c_size_t) for field in (
            'arena', 'ordblks', 'smblks', 'hblks', 'hblkhd', 'usmblks',
            'fsmblks', 'uordblks', 'fordblks', 'keepcost')]

    libc.mallinfo2.restype = Mallinfo2
    info = libc.mallinfo2()
    mb = 1024 * 1024
    return libc, {
        'heap_mb': round(info.arena / mb, 1),
        'heap_in_use_mb': round(info.uordblks / mb, 1),
        'heap_free_mb': round(info.fordblks / mb, 1),
        'mmapped_mb': round(info.hblkhd / mb, 1),
    }


def _short(obj):
    try:
        text = repr(obj)
    except Exception:  # noqa: BLE001 - any repr may fail in a probe
        text = '<unrepresentable>'
    return text[:120]


def _chain(target, exclude, depth=8):
    """Follow referrers from *target* until a module holds it, or nothing
    Python can see does: then a C reference (a GTK parent, a signal
    closure, a GObject toggle reference) keeps the last link alive.
    *exclude* holds the ids of the probe's own lists."""
    modules = {id(vars(m)): name for name, m in list(sys.modules.items())
               if m is not None}
    frame_type = type(sys._getframe())
    ignore = set(exclude)
    steps = []
    current = target
    seen = {id(target)}
    for _ in range(depth):
        found = gc.get_referrers(current)
        # The list just returned refers to *current* as well.
        ignore.add(id(found))
        referrers = [r for r in found
                     if id(r) not in ignore and id(r) not in seen
                     and not isinstance(r, frame_type)]
        if not referrers:
            steps.append('no Python referrer: held from C (a GTK parent, '
                         'a signal closure, or a toggle reference)')
            break
        ignore.add(id(referrers))
        holder = referrers[0]
        if id(holder) in modules:
            steps.append('module %s' % modules[id(holder)])
            break
        steps.append('%s %s%s' % (
            _type_name(type(holder)), _short(holder),
            ' (+%d more referrers)' % (len(referrers) - 1)
            if len(referrers) > 1 else ''))
        seen.add(id(holder))
        current = holder
    return steps


def _probe(options):
    report = {'id': options['id'], 'time': round(time.time(), 1)}
    report['gc_collected'] = gc.collect()
    report['gc_garbage'] = len(gc.garbage)
    report.update(_proc_status())

    objs = gc.get_objects()
    by_type = collections.Counter(type(o) for o in objs)
    report['babble_types'] = {
        _type_name(kind): count for kind, count in by_type.most_common()
        if _is_babble(kind)}
    report['top_types'] = {
        _type_name(kind): count for kind, count in by_type.most_common(15)}

    Gtk = sys.modules.get('gi.repository.Gtk')
    if Gtk is not None:
        tree = _widget_tree_counts(Gtk)
        unparented = {}
        for kind, count in by_type.items():
            if _is_babble(kind) and issubclass(kind, Gtk.Widget):
                name = _type_name(kind)
                if count > tree.get(name, 0):
                    unparented[name] = count - tree.get(name, 0)
        report['widgets_not_in_a_window'] = unparented

    report['containers'] = _containers()

    Gio = sys.modules.get('gi.repository.Gio')
    if Gio is not None:
        models = collections.defaultdict(list)
        for obj in objs:
            if isinstance(obj, Gio.ListModel) and _is_babble(type(obj)):
                models[_type_name(type(obj))].append(obj.get_n_items())
        report['list_models'] = {name: sorted(sizes, reverse=True)
                                 for name, sizes in models.items()}

    Gdk = sys.modules.get('gi.repository.Gdk')
    if Gdk is not None:
        textures = [o for o in objs if isinstance(o, Gdk.Texture)]
        report['textures'] = len(textures)
        report['texture_pixels_mb'] = round(sum(
            t.get_width() * t.get_height() * 4 for t in textures)
            / (1024 * 1024), 1)

    libc, heap = _mallinfo()
    report['malloc'] = heap
    if options.get('trim') and libc is not None:
        before = _proc_status()['rss_mb']
        libc.malloc_trim(0)
        report['trim_released_mb'] = before - _proc_status()['rss_mb']

    import tracemalloc
    if tracemalloc.is_tracing():
        snapshot = tracemalloc.take_snapshot().filter_traces(
            [tracemalloc.Filter(False, pattern) for pattern in _SKIP_TRACE])
        report['traced_mb'] = round(
            tracemalloc.get_traced_memory()[0] / (1024 * 1024), 1)
        previous = getattr(sys, '_babble_memprobe_snapshot', None)
        if previous is not None:
            growth = []
            for stat in snapshot.compare_to(previous, 'traceback')[:15]:
                if stat.size_diff <= 0:
                    continue
                frames = stat.traceback.format(limit=4, most_recent_first=True)
                growth.append({'kb': round(stat.size_diff / 1024, 1),
                               'blocks': stat.count_diff,
                               'where': [line.strip() for line in frames
                                         if line.strip()]})
            report['traced_growth'] = growth
        sys._babble_memprobe_snapshot = snapshot

    target = options.get('chain')
    if target:
        instances = [o for o in objs if _type_name(type(o)) == target
                     or type(o).__qualname__ == target]
        if Gtk is not None:
            # Widgets still in a window are not the suspects.
            instances = [o for o in instances
                         if not isinstance(o, Gtk.Widget)
                         or o.get_root() is None]
        suspects = instances[:3]
        exclude = {id(objs), id(instances), id(suspects)}
        report['chain'] = {
            'type': target, 'candidates': len(instances),
            'chains': [_chain(suspect, exclude) for suspect in suspects]}
        del instances, suspects

    del objs
    return report


try:
    _report = _probe(OPTIONS)  # noqa: F821 - prepended by memwatch.py
except Exception:  # noqa: BLE001 - report the failure instead of dying
    _report = {'id': OPTIONS['id'],  # noqa: F821
               'error': traceback.format_exc()}
with open(OPTIONS['out'], 'a') as _handle:  # noqa: F821
    _handle.write(json.dumps(_report) + '\n')
del _report
