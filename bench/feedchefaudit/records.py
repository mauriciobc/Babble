# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading Feed Chef audit logs, and putting a post back together from one.

A schema-2 record holds the facts of every post; the visible text lives in
the private sidecar beside the log. ``rebuild_status`` makes a status dict the
engine's own predicates read exactly as they read the original.
``patched`` stands in, for a post whose text is not whole (truncated or
the sidecar gone), the recorded verdicts of the two rules that read it.
"""

import contextlib
import html
import json
import os

from src.feedchef import audit as auditmod
from src.feedchef import contract, decisionlog
from src.feedchef import sentiment as sentimentmod
from src.feedchef.recipe import recipe_from_dict


class LogError(Exception):
    """A log that cannot be read or replayed; the message says why."""


class Loaded:
    def __init__(self, path, payload, record, sidecar):
        self.path = path
        self.payload = payload
        self.record = record
        self.sidecar = sidecar

    @property
    def timestamp(self):
        return float(self.payload.get('timestamp') or 0.0)


def default_dir():
    return decisionlog._dir()


def expand(paths):
    """The log files of *paths* (files, or folders searched one level),
    oldest first. Sidecars are not logs."""
    found = []
    for path in paths or [default_dir()]:
        if os.path.isdir(path):
            found.extend(os.path.join(path, name)
                         for name in os.listdir(path)
                         if name.startswith('session_')
                         and name.endswith('.json')
                         and not name.endswith('.text.json'))
        else:
            found.append(path)
    return sorted(found, key=lambda path: (os.path.getmtime(path), path))


def load(path):
    try:
        with open(path, encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as error:
        raise LogError('cannot read %s: %s' % (os.path.basename(path),
                                               error)) from error
    record = payload.get('audit') if isinstance(payload, dict) else None
    sidecar = None
    side = auditmod.sidecar_path(path)
    if os.path.isfile(side):
        try:
            with open(side, encoding='utf-8') as handle:
                sidecar = json.load(handle)
        except (OSError, ValueError):
            sidecar = None
    return Loaded(path, payload, record, sidecar)


def recipe_of(record):
    recipe = recipe_from_dict(record['recipe']['snapshot'])
    if auditmod.recipe_hash(recipe) != record['recipe']['hash']:
        raise LogError('the recipe snapshot does not match its hash')
    return recipe


def check_replayable(loaded, force=False):
    """Raise LogError unless the record can be replayed or evaluated."""
    record = loaded.record
    if not isinstance(record, dict) or record.get('schema') != \
            auditmod.SCHEMA:
        raise LogError('no schema-%d audit record in this log'
                       % auditmod.SCHEMA)
    if record.get('engine_rev') != auditmod.ENGINE_REV and not force:
        raise LogError('made by engine revision %s, this is %s (--force '
                       'replays anyway)' % (record.get('engine_rev'),
                                            auditmod.ENGINE_REV))
    if not (record.get('clock') or {}).get('now_utc'):
        raise LogError('the record has no clock')


def text_of(loaded, entry):
    """(text row, whole): the sidecar row of a post, and whether it holds
    all of the text."""
    row = (loaded.sidecar or {}).get(entry['facts']['text_ref'])
    if row is None:
        return {'text': '', 'spoiler': ''}, False
    return row, not row.get('text_truncated')


def rebuild_status(entry, row):
    facts = entry['facts']
    content = {
        'id': entry['cid'] if facts['boost'] else entry['id'],
        'created_at': facts['created_c'],
        'content': '<p>%s</p>' % html.escape(row.get('text') or '',
                                             quote=False),
        'spoiler_text': row.get('spoiler') or '',
        'sensitive': facts['sensitive'], 'language': facts['lang'],
        'favourites_count': facts['fav'], 'reblogs_count': facts['reblog'],
        'replies_count': facts['reply'],
        'account': {'id': facts['author_id']},
        'tags': [{'name': name} for name in facts['tags']],
        'media_attachments': [
            {'id': str(index), 'type': 'image',
             'description': 'alt' if index < facts['alt'] else ''}
            for index in range(facts['media'])],
    }
    if not facts['boost']:
        return content
    return {'id': entry['id'], 'created_at': facts['created_w'],
            'account': {'id': facts['booster_id']}, 'reblog': content,
            'content': '', 'tags': [], 'media_attachments': []}


class Rebuilt:
    """The statuses of one record, and what a replay needs besides them."""

    def __init__(self, loaded):
        self.loaded = loaded
        self.record = loaded.record
        self.recipe = recipe_of(self.record)
        self.statuses = {}
        self.unreliable = set()
        self.sentiment = {}
        self.text_len = {}
        self.keyword_failed = set()
        for entry in self.record['posts']:
            sid = entry['id']
            row, whole = text_of(loaded, entry)
            self.statuses[sid] = rebuild_status(entry, row)
            self.text_len[sid] = entry['facts']['text_len']
            if not whole:
                self.unreliable.add(sid)
            if entry['rules'].get('keyword_blocklist') == 'fail':
                self.keyword_failed.add(sid)
            if entry['sent'] is not None:
                self.sentiment[sid] = entry['sent']['score']

    def entries(self):
        return self.record['posts']

    def follow_ids(self):
        return {entry['facts']['author_id'] for entry in self.entries()
                if entry['follow']['verdict'] == 'follows'}

    def drift(self):
        """Posts whose recorded sentiment the analyser, run again on the
        rebuilt text, no longer gives (whole text only)."""
        drifted = []
        for sid, recorded in self.sentiment.items():
            if sid in self.unreliable or recorded is None:
                continue
            again = sentimentmod.score_status(self.statuses[sid])
            if again is None or abs(again - recorded) > 1e-9:
                drifted.append(sid)
        return drifted


def _sid(status):
    return str(status.get('id') or '')


@contextlib.contextmanager
def patched(rebuilt):
    """While inside, a post is judged by its recorded sentiment and a post
    whose text is not whole by its recorded verdicts of the two rules that
    read the text (blocked words, minimum length)."""
    real = (contract.blocked_hit, contract.text_length, contract.post_text,
            sentimentmod.score_status)

    def blocked_hit(status, words):
        if _sid(status) in rebuilt.unreliable:
            return 'recorded' if _sid(status) in rebuilt.keyword_failed \
                else None
        return real[0](status, words)

    def text_length(status, native_bluesky=False):
        if _sid(status) in rebuilt.unreliable:
            return rebuilt.text_len[_sid(status)]
        return real[1](status, native_bluesky)

    def post_text(status, native_bluesky=False):
        if _sid(status) in rebuilt.unreliable:
            return ''
        return real[2](status, native_bluesky)

    def score_status(status):
        return rebuilt.sentiment.get(_sid(status))

    contract.blocked_hit = blocked_hit
    contract.text_length = text_length
    contract.post_text = post_text
    sentimentmod.score_status = score_status
    try:
        yield
    finally:
        (contract.blocked_hit, contract.text_length, contract.post_text,
         sentimentmod.score_status) = real
