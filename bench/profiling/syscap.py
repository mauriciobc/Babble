# SPDX-License-Identifier: GPL-3.0-or-later
"""Minimal .syscap reader: frame headers, processes, marks, samples.

Layout from libsysprof-capture/sysprof-capture-types.h (sysprof 51):
256-byte file header, then 8-byte-aligned frames with a 24-byte header.
"""
import collections
import mmap
import struct
import sys

HEADER = 256
FRAME = struct.Struct('<HhiqI4x')       # len, cpu, pid, time, type(+pad bits)
MAGIC = 0xFDCA975E

TYPES = {1: 'timestamp', 2: 'sample', 3: 'map', 4: 'process', 5: 'fork',
         6: 'exit', 7: 'jitmap', 8: 'ctrdef', 9: 'ctrset', 10: 'mark',
         11: 'metadata', 12: 'log', 13: 'file_chunk', 14: 'allocation',
         15: 'overlay', 16: 'trace', 17: 'dbus_message'}


def cstr(buf):
    return bytes(buf).split(b'\0', 1)[0].decode('utf-8', 'replace')


def frames(path):
    """Yield (type, cpu, pid, time_ns, payload memoryview)."""
    with open(path, 'rb') as handle:
        data = mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ)
        view = memoryview(data)
        magic, = struct.unpack_from('<I', data, 0)
        if magic != MAGIC:
            raise SystemExit('not a syscap: magic %#x' % magic)
        start, end = struct.unpack_from('<qq', data, 72)
        yield ('header', 0, 0, start, end)
        offset = HEADER
        size = len(data)
        while offset + FRAME.size <= size:
            length, cpu, pid, time, kind = FRAME.unpack_from(data, offset)
            if length < FRAME.size:
                break               # truncated / zero padding at the end
            kind &= 0xFF
            yield (kind, cpu, pid, time,
                   view[offset + FRAME.size:offset + length])
            offset += length


def mark(payload):
    duration, = struct.unpack_from('<q', payload, 0)
    return (duration, cstr(payload[8:32]), cstr(payload[32:72]),
            cstr(payload[72:]))


def app_pid(path):
    """The process that emitted the most GTK marks: the app under test.

    Not the process whose command line says ``python``: with sysprof's
    sampler off (``--no-perf``) there are no process frames for it, and
    under ``perf record`` the recorded command line is perf's.
    """
    counts = collections.Counter()
    for kind, _cpu, pid, _t, payload in frames(path):
        if kind == 10 and cstr(payload[8:32]) == 'GTK':
            counts[pid] += 1
    if not counts:
        raise SystemExit('%s: no GTK marks in this capture' % path)
    return counts.most_common(1)[0][0]


def sample(payload):
    n_addrs, = struct.unpack_from('<H', payload, 0)
    tid, = struct.unpack_from('<i', payload, 4)
    addrs = struct.unpack_from('<%dQ' % n_addrs, payload, 8)
    return tid, addrs


if __name__ == '__main__':
    import collections
    counts = collections.Counter()
    for item in frames(sys.argv[1]):
        if item[0] == 'header':
            print('capture start %d end %d' % item[3:5])
            continue
        counts[TYPES.get(item[0], item[0])] += 1
    for name, n in counts.most_common():
        print('%-14s %d' % (name, n))
