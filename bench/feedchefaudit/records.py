# SPDX-License-Identifier: GPL-3.0-or-later
"""Reading Feed Chef audit logs, and putting a post back together from one.

A schema-2 record holds the facts of every post; the visible text lives in
the private sidecar beside the log. ``rebuild_status`` makes a status dict the
engine's own predicates read exactly as they read the original.
``patched`` stands in, for a post whose text is not whole (truncated or
the sidecar gone), the recorded verdicts of the two rules that read it.
"""

import contextlib
import hashlib
import html
import json
import os

from feedchefaudit import validate
from src.feedchef import audit as auditmod
from src.feedchef import (contract, decisionlog, deps, langguess,
                         textutil)
from src.feedchef import relevance as relevancemod
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
    return sorted(found, key=lambda path: (_mtime(path), path))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


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
        if not isinstance(sidecar, dict):
            sidecar = None
    return Loaded(path, payload, record, sidecar)


def recipe_of(record):
    try:
        recipe = recipe_from_dict(record['recipe']['snapshot'])
    except Exception as error:
        raise LogError('the recipe snapshot cannot be read (%s)'
                       % type(error).__name__) from error
    if auditmod.recipe_hash(recipe) != record['recipe']['hash']:
        raise LogError('the recipe snapshot does not match its hash')
    return recipe


def check_replayable(loaded, force=False, replay=False):
    """Raise LogError unless the record can be replayed or evaluated. A
    replay (*replay*) also needs the libraries the record's grouping used:
    cooking it again without them would call a missing package a
    mismatch."""
    record = loaded.record
    if not isinstance(record, dict) or record.get('schema') != \
            auditmod.SCHEMA:
        raise LogError('no schema-%d audit record in this log'
                       % auditmod.SCHEMA)
    if record.get('engine_rev') != auditmod.ENGINE_REV and not force:
        raise LogError('made by engine revision %s, this is %s (--force '
                       'replays anyway)' % (record.get('engine_rev'),
                                            auditmod.ENGINE_REV))
    problems = validate.structure(record)
    if problems:
        raise LogError('malformed audit record: %s' % problems[0])
    page = record.get('cluster')
    if replay and isinstance(page, dict) and page.get('status') == 'ok' \
            and record['outcome']['status'] in auditmod.FINISHED:
        missing = [name for name in ('kmedoids', 'numpy')
                   if deps.unavailable_reason(name)]
        if missing:
            raise LogError('cannot be replayed here: %s missing'
                           % missing[0])


def structure_problem(record):
    """Why *record* (the ``audit`` of a log) cannot be read, or None."""
    if not isinstance(record, dict) or record.get('schema') != \
            auditmod.SCHEMA:
        return 'no schema-%d audit record in this log' % auditmod.SCHEMA
    problems = validate.structure(record)
    return 'malformed audit record: %s' % problems[0] if problems else None


def text_of(loaded, entry):
    """(text row, whole, verified): the sidecar row of a post, whether it
    holds all of the text, and whether it is the text the record was made
    with (its sha256 is the recorded one). A row that is missing, not a
    row, or not verified is empty: nothing reads text that cannot be
    vouched for."""
    row = (loaded.sidecar or {}).get(entry['facts']['text_ref'])
    empty = {'text': '', 'spoiler': ''}
    if not (isinstance(row, dict) and isinstance(row.get('text'), str)
            and isinstance(row.get('spoiler'), str)):
        return empty, False, False
    if auditmod.text_digest(row['text'], row['spoiler']) \
            != entry['facts']['text_sha256']:
        return empty, False, False
    return row, not row.get('text_truncated'), True


def rebuild_status(entry, row):
    facts = entry['facts']
    host = facts.get('host') or ''
    # The server the account that put the post in the feed is on (the
    # 'instance' dimension of the cap reads it from the account's url).
    seat = {'url': 'https://%s/@x' % host} if host else {}
    content = {
        'id': entry['cid'] if facts['boost'] else entry['id'],
        'created_at': facts['created_c'],
        'content': '<p>%s</p>' % html.escape(row.get('text') or '',
                                             quote=False),
        'spoiler_text': row.get('spoiler') or '',
        'sensitive': facts['sensitive'], 'language': facts['lang'],
        'favourites_count': facts['fav'], 'reblogs_count': facts['reblog'],
        'replies_count': facts['reply'],
        'account': {'id': facts['author_id'], **({} if facts['boost']
                                                  else seat)},
        'tags': [{'name': name} for name in facts['tags']],
        'media_attachments': [
            {'id': str(index), 'type': 'image',
             'description': 'alt' if index < facts['alt'] else ''}
            for index in range(facts['media'])],
    }
    if not facts['boost']:
        return content
    return {'id': entry['id'], 'created_at': facts['created_w'],
            'account': {'id': facts['booster_id'], **seat},
            'reblog': content,
            'content': '', 'tags': [], 'media_attachments': []}


class Rebuilt:
    """The statuses of one record, and what a replay needs besides them."""

    def __init__(self, loaded):
        self.loaded = loaded
        self.record = loaded.record
        self.recipe = recipe_of(self.record)
        self.statuses = {}
        self.unreliable = set()
        self.unverified = set()
        self.sentiment = {}
        self.text_len = {}
        self.keyword_failed = set()
        # The posts the grouping's strategy keeps, worked out once by
        # evaluate (keep_known: asked for yet; None: cannot be told).
        self.keep = None
        self.keep_known = False
        # The language each post was guessed to be in (if it was).
        self.guesses = {}
        # The words the relevance sort and the grouping read, by post, and
        # the posts whose words are not whole or not the recorded ones.
        self.words = {}
        self.words_unreliable = set()
        for entry in self.record['posts']:
            sid = entry['id']
            row, whole, verified = text_of(loaded, entry)
            self._words(entry, row)
            if not verified:
                self.unverified.add(sid)
            self.statuses[sid] = rebuild_status(entry, row)
            self.text_len[sid] = entry['facts']['text_len']
            if not whole:
                self.unreliable.add(sid)
            if entry['rules'].get('keyword_blocklist') == 'fail':
                self.keyword_failed.add(sid)
            if entry['sent'] is not None:
                self.sentiment[sid] = entry['sent']['score']
            found = entry['facts'].get('lang_guess')
            if isinstance(found, dict):
                self.guesses[sid] = (found['lang'], found['p'])

    def _words(self, entry, row):
        """The focus text of a post, when the record kept it (revision 3,
        for a recipe with a focus or grouping) and the sidecar still holds
        what it hashed to."""
        facts = entry['facts']
        digest = facts.get('focus_sha256')
        if digest is None:
            return
        words = row.get('focus')
        if not (isinstance(words, str) and not row.get('focus_truncated')
                and hashlib.sha256(words.encode('utf-8')).hexdigest()
                == digest):
            self.words_unreliable.add(entry['id'])
            return
        self.words[entry['id']] = words

    def needs_words(self):
        """Whether the recipe has a stage that reads the words of posts."""
        kitchen = self.recipe.kitchen
        return bool(relevancemod.focus_keywords(kitchen)) or any(
            mod.type == 'similarity_cluster' for mod in kitchen.modifiers)

    def words_whole(self):
        """Whether every post's words are known (or no stage needs them)."""
        return not self.needs_words() or (
            not self.words_unreliable
            and len(self.words) == len(self.record['posts']))

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
        return drifted + self.guess_drift()

    def guess_drift(self):
        """Posts whose recorded language guess the guesser, run again on the
        rebuilt text, no longer gives (whole text only). A warning, as for
        sentiment: the replay itself feeds the recorded guess back."""
        drifted = []
        for sid, recorded in self.guesses.items():
            if sid in self.unreliable:
                continue
            again = langguess.guess(self.statuses[sid])
            if again is None or again[0] != recorded[0] \
                    or abs(again[1] - recorded[1]) > 1e-9:
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
            sentimentmod.score_status, textutil.focus_text, langguess.guess)

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

    def guess(status):
        return rebuilt.guesses.get(_sid(status))

    def focus_text(status):
        found = rebuilt.words.get(_sid(status))
        return real[4](status) if found is None else found

    contract.blocked_hit = blocked_hit
    contract.text_length = text_length
    contract.post_text = post_text
    sentimentmod.score_status = score_status
    textutil.focus_text = focus_text
    langguess.guess = guess
    try:
        yield
    finally:
        (contract.blocked_hit, contract.text_length, contract.post_text,
         sentimentmod.score_status, textutil.focus_text,
         langguess.guess) = real
