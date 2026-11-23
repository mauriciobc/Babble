# SPDX-License-Identifier: GPL-3.0-or-later
"""``show``: one audit log as a report a person can read.

No post text unless asked (``--text``): the log is the reader's own and stays
on their machine, but the report is the part people paste into an issue.
"""

from feedchefaudit.records import LogError, check_replayable, load


def _factor(value):
    return 'x%.4g' % value


def _line(entry):
    score = entry['score']
    if score is None:
        return 'not scored'
    return 'base %.4g  fresh %s  sentiment %s  trending %s  final %.6g' % (
        score['base'], _factor(score['fresh']), _factor(score['sent']),
        _factor(score['trend']), score['final'])


def _origin(entry):
    return ' + '.join([entry['origin'], *entry['also_in']])


def _text(loaded, entry):
    row = (loaded.sidecar or {}).get(entry['facts']['text_ref'])
    if row is None:
        return '(no text kept)'
    more = ' (cut at %d characters)' % len(row['text']) \
        if row.get('text_truncated') else ''
    return '%s%s' % (row['text'], more)


def _post(entry, loaded, text, out):
    out('post %s' % entry['id'])
    out('  from: %s' % _origin(entry))
    removal = entry['removal']
    if removal:
        out('  removed at %s: %s%s' % (
            removal['stage'], removal['reason'],
            ' (also %s)' % ', '.join(removal['also'])
            if removal['also'] else ''))
    else:
        out('  selected, rank %d' % (entry['rank'] + 1))
    out('  follow: %s' % entry['follow']['verdict'])
    for promise, verdict in entry['rules'].items():
        if verdict != 'n/a':
            out('  rule %-20s %s' % (promise, verdict))
    if entry['sent']:
        out('  sentiment: %s %s' % (entry['sent']['engine'],
                                    entry['sent']['score']))
    out('  score: %s' % _line(entry))
    facts = entry['facts']
    out('  facts: %d fav, %d boosts, %d replies, %d media (%d with alt), '
        'lang %s, %d characters' % (
            facts['fav'], facts['reblog'], facts['reply'], facts['media'],
            facts['alt'], facts['lang'], facts['text_len']))
    if text:
        out('  text: %s' % _text(loaded, entry))


def show(path, post=None, text=False, out=print):
    """Print the report of *path*; 0, or 2 for a log it cannot read."""
    try:
        loaded = load(path)
        check_replayable(loaded, force=True)
    except LogError as error:
        out('%s' % error)
        return 2
    record = loaded.record
    recipe = record['recipe']
    snapshot = recipe['snapshot']
    out('recipe: %s  (hash %s)  scope %s' % (
        snapshot['meta']['name'], recipe['hash'][:12],
        snapshot['pantry']['scope']))
    out('app %s  schema %d  engine revision %d' % (
        record['app']['version'], record['schema'], record['engine_rev']))
    out('clock: %s' % record['clock']['now_utc'])
    out('cook %s  page %d  hop %d' % (
        record['cook_id'], record['page_index'], record['hop']))
    summary = record['summary']
    out('posts: %d fetched, %d in the pantry, %d selected' % (
        summary['raw_fetched'], summary['pantry_in'], summary['selected']))
    if record['outcome']['status'] != 'ok':
        out('the cook failed: %s' % record['outcome']['error'])
        return 0
    if post:
        found = [entry for entry in record['posts']
                 if entry['id'].endswith(post)]
        if not found:
            out('no post %s in this log' % post)
            return 2
        for entry in found:
            _post(entry, loaded, text, out)
        return 0
    out('removed, by reason:')
    for reason, count in sorted(summary['by_reason'].items(),
                                key=lambda item: -item[1]):
        out('  %5d  %s' % (count, reason))
    out('selected, in order:')
    by_id = {entry['id']: entry for entry in record['posts']}
    for index, sid in enumerate(record['outputs']['order']):
        entry = by_id[sid]
        out('  %2d. %s  %s  from %s' % (index + 1, sid[-8:], _line(entry),
                                       _origin(entry)))
        if text:
            out('      %s' % _text(loaded, entry))
    out('removed:')
    for entry in record['posts']:
        removal = entry['removal']
        if removal:
            out('  %s  %s: %s' % (entry['id'][-8:], removal['stage'],
                                  removal['reason']))
    return 0
