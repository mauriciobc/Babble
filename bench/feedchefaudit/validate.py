# SPDX-License-Identifier: GPL-3.0-or-later
"""What a record must look like, and what it must say about itself, before
anything reads it.

``structure`` is the shape (keys and types), checked first so that nothing
downstream indexes into a record that is not one. ``consistency`` is what
the record claims about its own numbers: every post is selected or removed
once, and the summary adds up. Both return the problems found, as lines a
person can read; an empty list is a sound record. Neither needs the engine,
the recipe or the sidecar.
"""

from datetime import datetime

from src.feedchef import audit as auditmod

_NONE = type(None)
_NUM = (int, float)
STATUSES = ('ok', 'failed', 'cancelled')

TOP = {'schema': int, 'engine_rev': int, 'session_id': str, 'cook_id': str,
       'page_index': int, 'hop': int, 'cursor_in': (str, _NONE),
       'cursor_out': (str, _NONE), 'app': dict, 'recipe': dict,
       'account': dict, 'clock': dict, 'env': dict, 'params': dict,
       'sources': dict, 'outcome': dict, 'summary': dict, 'outputs': dict,
       'posts': list}
RECIPE = {'id': str, 'hash': str, 'snapshot': dict}
ENV = {'self_id': str, 'native_bluesky': bool, 'viewer_filter': bool,
       'follow': dict, 'trends': dict, 'local_heat': dict}
SUMMARY = {'raw_fetched': int, 'pantry_in': int, 'duplicates': int,
           'no_id': int, 'selected': int, 'guard_checked': int,
           'by_reason': dict}
POST = {'id': str, 'cid': str, 'origin': str, 'also_in': list,
        'facts': dict, 'follow': dict, 'viewer': (str, _NONE),
        'rules': dict, 'sent': (dict, _NONE), 'score': (dict, _NONE),
        'removal': (dict, _NONE), 'rank': int}
FACTS = {'created_w': (str, _NONE), 'created_c': (str, _NONE),
         'fav': _NUM, 'reblog': _NUM, 'reply': _NUM, 'tags': list,
         'lang': (str, _NONE), 'media': int, 'alt': int, 'sensitive': bool,
         'text_len': int, 'text_ref': str, 'text_sha256': str,
         'author_id': (str, _NONE), 'booster_id': (str, _NONE),
         'boost': bool}
SCORE = {'base': _NUM, 'fresh': _NUM, 'sent': _NUM, 'trend': _NUM,
         'final': (*_NUM, _NONE)}


def _typed(value, kinds):
    kinds = kinds if isinstance(kinds, tuple) else (kinds,)
    if isinstance(value, bool) and bool not in kinds:
        return False
    return isinstance(value, kinds)


def _need(problems, where, obj, spec):
    """Whether *obj* is an object with every key of *spec*, typed."""
    if not isinstance(obj, dict):
        problems.append('%s is not an object' % where)
        return False
    sound = True
    for key, kinds in spec.items():
        if key not in obj:
            problems.append('%s has no %s' % (where, key))
            sound = False
        elif not _typed(obj[key], kinds):
            problems.append('%s.%s has the wrong type' % (where, key))
            sound = False
    return sound


def _snapshot(problems, snapshot):
    meta, pantry = snapshot.get('meta'), snapshot.get('pantry')
    if not (isinstance(meta, dict) and isinstance(meta.get('name'), str)):
        problems.append('recipe.snapshot has no meta.name')
    if not (isinstance(pantry, dict) and isinstance(pantry.get('scope'),
                                                      str)):
        problems.append('recipe.snapshot has no pantry.scope')


def _post(problems, index, entry):
    where = 'posts[%d]' % index
    if not _need(problems, where, entry, POST):
        return
    _need(problems, where + '.facts', entry['facts'], FACTS)
    if not _need(problems, where + '.follow', entry['follow'],
                 {'verdict': str}):
        return
    group = entry.get('cluster')
    if group is not None:
        _need(problems, where + '.cluster', group,
              {'id': int, 'medoid': str})
    for key, spec in (('score', SCORE), ('removal', {
            'stage': str, 'reason': str, 'also': list}), ('sent', {
            'engine': object, 'score': (*_NUM, _NONE)})):
        if entry[key] is not None:
            _need(problems, '%s.%s' % (where, key), entry[key], spec)
    if not all(isinstance(rule, str) for rule in entry['rules'].values()):
        problems.append('%s.rules has the wrong type' % where)


def _sources(problems, sources):
    scope, tags, focus = (sources.get('scope'), sources.get('hashtag'),
                          sources.get('focus'))
    if not (isinstance(scope, list) and isinstance(tags, dict)
            and isinstance(focus, dict)
            and all(isinstance(v, list) for v in (*tags.values(),
                                                  *focus.values()))):
        problems.append('sources has the wrong shape')


def structure(record):
    """The problems with the shape of *record* (empty when it is sound).
    Stops at the first level that is broken, so the lines stay few."""
    problems = []
    if not isinstance(record, dict):
        return ['the audit record is not an object']
    if not _need(problems, 'the record', record, TOP):
        return problems
    if not (_need(problems, 'recipe', record['recipe'], RECIPE)
            and _need(problems, 'env', record['env'], ENV)
            and _need(problems, 'summary', record['summary'], SUMMARY)
            and _need(problems, 'outputs', record['outputs'],
                      {'order': list, 'author_counts_out': dict})
            and _need(problems, 'outcome', record['outcome'],
                      {'status': str, 'error': str})
            and _need(problems, 'params', record['params'],
                      {'count': int, 'author_counts_in': dict})
            and _need(problems, 'account', record['account'],
                      {'id': str, 'instance': str})
            and _need(problems, 'clock', record['clock'],
                      {'now_utc': (str, _NONE)})):
        return problems
    _snapshot(problems, record['recipe']['snapshot'])
    _sources(problems, record['sources'])
    # Revision 3 added these; a record of revision 2 has none of them.
    for where, key in (('params', 'instance_counts_in'),
                       ('outputs', 'instance_counts_out')):
        if key in record[where] and not isinstance(record[where][key], dict):
            problems.append('%s.%s has the wrong type' % (where, key))
    if 'cluster' in record and not _need(
            problems, 'cluster', record['cluster'],
            {'status': str, 'n': int, 'k': int, 'strategy': str,
             'max_per': int, 'init': str, 'seed': int, 'weights': list}):
        return problems
    if record['outcome']['status'] not in STATUSES:
        problems.append('outcome.status is not one of %s' % ', '.join(
            STATUSES))
    if not all(isinstance(sid, str) for sid in record['outputs']['order']):
        problems.append('outputs.order has an id that is not text')
    if record['outcome']['status'] == 'ok':
        try:
            datetime.fromisoformat(record['clock']['now_utc'])
        except (TypeError, ValueError):
            problems.append('the clock is not a date')
    for index, entry in enumerate(record['posts']):
        _post(problems, index, entry)
        if len(problems) > 8:
            break
    return problems


def _selected(record, problems, by_id):
    """The posts the order names must be posts of the record, once each,
    scored, kept and ranked as the order says."""
    order = record['outputs']['order']
    if len(set(order)) != len(order):
        problems.append('outputs.order names a post twice')
    for position, sid in enumerate(order):
        entry = by_id.get(sid)
        if entry is None:
            problems.append('outputs.order names a post (%s) that is not in '
                            'posts' % sid[-6:])
            continue
        if entry['removal'] is not None:
            problems.append('post %s is selected and removed' % sid[-6:])
        if entry['rank'] != position:
            problems.append('post %s has rank %d, its place in the order '
                            'is %d' % (sid[-6:], entry['rank'], position))
        if entry['score'] is None or entry['score']['final'] is None:
            problems.append('selected post %s has no score' % sid[-6:])


def _removed(record, problems, entry, counted):
    removal = entry['removal']
    tail = entry['id'][-6:]
    if removal is None:
        problems.append('post %s is neither selected nor removed' % tail)
        return
    reason = removal['reason']
    if not auditmod.is_reason(reason) or not all(
            isinstance(also, str) and auditmod.is_reason(also)
            for also in removal['also']):
        problems.append('post %s has a removal reason that is not in the '
                        'closed set' % tail)
        return
    counted[reason] = counted.get(reason, 0) + 1
    if entry['rank'] != -1:
        problems.append('removed post %s has rank %d' % (tail,
                                                        entry['rank']))
    viewer = entry['viewer']
    asked = record['env']['viewer_filter']
    if reason == 'viewer_hide' and not (asked and viewer == 'hidden'):
        problems.append('post %s was removed by the viewer filter, which '
                        'the record does not show it hid' % tail)
    elif reason != 'viewer_hide' and viewer == 'hidden':
        problems.append('post %s is marked hidden by the viewer filter but '
                        'removed for %s' % (tail, reason))


def consistency(record):
    """What a structurally sound record says that cannot be true."""
    problems = []
    status = record['outcome']['status']
    posts = record['posts']
    order = record['outputs']['order']
    if status == 'failed' and (order or any(
            entry['rank'] >= 0 for entry in posts)):
        return ['failed cook with output']
    if status != 'ok':
        return problems
    by_id = {}
    for entry in posts:
        if entry['id'] in by_id:
            problems.append('post %s is in the record twice'
                            % entry['id'][-6:])
        by_id[entry['id']] = entry
    summary = record['summary']
    _selected(record, problems, by_id)
    selected = set(order)
    counted = {}
    for entry in posts:
        if entry['id'] not in selected:
            _removed(record, problems, entry, counted)
        elif entry['viewer'] == 'hidden':
            problems.append('post %s is selected and hidden' % entry['id'][-6:])
    if not record['env']['viewer_filter'] and any(
            entry['viewer'] is not None for entry in posts):
        problems.append('a post has a viewer verdict but there was no '
                        'viewer filter')
    if summary['pantry_in'] != len(posts):
        problems.append('summary.pantry_in is %d, the record has %d posts'
                        % (summary['pantry_in'], len(posts)))
    if summary['selected'] != len(order):
        problems.append('summary.selected is %d, outputs.order has %d'
                        % (summary['selected'], len(order)))
    if summary['pantry_in'] != len(order) + sum(summary['by_reason'].values()):
        problems.append('summary.pantry_in is not selected plus the removals')
    if summary['by_reason'] != counted:
        problems.append('summary.by_reason is not the removals counted')
    if counted.get('cluster_cap') and (record.get('cluster') or {}).get(
            'status') != 'ok':
        problems.append('posts were removed by the grouping, which the '
                        'record does not show ran')
    guard = sum(count for reason, count in counted.items()
                if reason.startswith('guard:'))
    if summary['guard_checked'] != len(order) + guard:
        problems.append('summary.guard_checked is %d, the guard was given '
                        '%d posts' % (summary['guard_checked'],
                                      len(order) + guard))
    sources = record['sources']
    slots = [*sources['scope'], *(slot for found in sources['hashtag'].values()
                                  for slot in found),
             *(slot for found in sources['focus'].values()
               for slot in found)]
    if summary['raw_fetched'] != len(slots):
        problems.append('summary.raw_fetched is %d, the sources list %d'
                        % (summary['raw_fetched'], len(slots)))
    for slot in slots:
        if isinstance(slot, str) and slot not in by_id:
            problems.append('a source lists a post (%s) that is not in '
                            'posts' % slot[-6:])
            break
    return problems
