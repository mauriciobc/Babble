# SPDX-License-Identifier: GPL-3.0-or-later
"""``langbench``: how well does a language detector agree with the declared
language of the posts Feed Chef has seen?

**Precision here is measured against the author's declared language, which is
noisy**: clients often default the field (en is the usual default), so some
"errors" are wrong labels rather than a wrong detector, and the figures are a
lower-bound proxy, not ground truth.

The labelled set is every post of the audit logs that declares a language
(Mastodon's ``language`` field, the primary subtag), once per distinct
verified text. The detector is py3langid with ``norm_probs=True``, restricted
to a language set, and a post is judged only when the top probability reaches
the threshold; below it the detector abstains. Two sets are reported:
``RUNTIME_LANGS`` (what Feed Chef will restrict the detector to; the declared
languages seen in the data are a reasonable proxy for the languages the
viewer's timeline holds) and ``COMMON`` (a fixed list of widespread languages,
as a second opinion). For ``en`` and ``pt`` it reports precision, recall and
support at each threshold, a Wilson 95% lower bound of precision, coverage
(the share of posts judged), and by text length (<40, 40-120, >120 characters).

A threshold is only chosen where each target language has at least
``MIN_PREDICTED`` judged posts, and by the Wilson lower bound, not the point
estimate. A hold-out checks it: posts are split in two halves by author (a
hash of the author id, so no author is in both), the threshold is picked on
one half and its precision is reported on the other, both ways.

Output is numbers only: no post id, handle or text is ever printed.
simplemma's detector is compared when it is importable (``--simplemma``).
"""

import hashlib
import math
import re
import sys

from feedchefaudit.records import (LogError, expand, load, structure_problem,
                                   text_of)

# The set Feed Chef restricts py3langid to at runtime: the declared
# languages seen in the data (every one the model knows), a proxy for what
# the viewer's timeline holds.
RUNTIME_LANGS = ('en', 'pt', 'es', 'de', 'fr', 'tr', 'it', 'ar', 'nl', 'ca',
                 'fi', 'gl', 'eo', 'da', 'pl', 'nn')
COMMON = ('en', 'pt', 'es', 'fr', 'de', 'it', 'ja', 'ko', 'zh', 'ru', 'nl',
          'ar', 'pl', 'uk', 'sv', 'tr')
TABLE_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.97, 0.99)
TARGETS = ('en', 'pt')
PRECISION_GOAL = 0.97
MIN_PREDICTED = 30
BUCKETS = (('<40', 0, 40), ('40-120', 40, 121), ('>120', 121, 10 ** 9))
_URL = re.compile(r'https?://\S+|www\.\S+')
_MENTION = re.compile(r'(?<!\w)@[\w.-]+(?:@[\w.-]+)?')


def clean(text):
    """The text a detector should read: no links, no handles."""
    text = _MENTION.sub(' ', _URL.sub(' ', text))
    return ' '.join(text.split())


def primary(code):
    """'pt-BR' -> 'pt'; None for an empty or odd declaration."""
    if not isinstance(code, str):
        return None
    head = code.strip().lower().replace('_', '-').split('-')[0]
    return head if 2 <= len(head) <= 3 and head.isalpha() else None


def wilson_lower(hits, total, z=1.96):
    """The lower end of the Wilson 95% interval of a proportion."""
    if not total:
        return 0.0
    p = hits / total
    denom = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total))
    return (centre - margin) / denom


def labelled_posts(paths):
    """[(declared language, cleaned text, author id)] once per distinct
    verified text, plus counts of what was read and skipped."""
    seen, rows = set(), []
    stats = {'logs': 0, 'posts': 0, 'duplicate': 0, 'no_lang': 0,
             'no_text': 0, 'unreadable': 0}
    for path in expand(paths):
        try:
            loaded = load(path)
        except LogError:
            continue
        record = loaded.record
        if structure_problem(record):
            stats['unreadable'] += 1
            continue
        stats['logs'] += 1
        for entry in record.get('posts') or ():
            stats['posts'] += 1
            facts = entry.get('facts') or {}
            row, _whole, verified = text_of(loaded, entry)
            digest = facts.get('text_sha256')
            lang = primary(facts.get('lang'))
            text = clean(row.get('text') or '') if verified else ''
            if digest is not None and digest in seen:
                stats['duplicate'] += 1
                continue
            if not lang:
                stats['no_lang'] += 1
            elif not text:
                stats['no_text'] += 1
            else:
                # Only a verified text with a language is a sample, so
                # only it makes its digest "seen"; a post with no digest
                # is never collapsed with another.
                if digest is not None:
                    seen.add(digest)
                rows.append((lang, text, str(facts.get('author_id') or '')))
    return rows, stats


def half_of(author):
    """0 or 1 by a stable hash of the author id (an unknown author goes
    to 0)."""
    return hashlib.sha256(author.encode()).digest()[0] & 1


def _bucket(text):
    for name, low, high in BUCKETS:
        if low <= len(text) < high:
            return name
    return BUCKETS[-1][0]


def predict_langid(rows, langs):
    from py3langid.langid import MODEL_FILE, LanguageIdentifier
    ident = LanguageIdentifier.from_model_file(MODEL_FILE, norm_probs=True)
    use = sorted(set(langs) & set(ident.labels))
    ident.set_languages(use)
    return [ident.classify(row[1]) for row in rows], use


def predict_simplemma(rows, langs):
    import simplemma
    use = []
    for code in sorted(langs):
        try:
            simplemma.langdetect('probe', lang=(code,))
        except ValueError:
            continue  # a language simplemma has no dictionary for
        use.append(code)
    out = []
    for row in rows:
        ranked = simplemma.langdetect(row[1], lang=tuple(use))
        if ranked and ranked[0][0] not in ('unk', ''):
            out.append(ranked[0])
        else:
            out.append(('und', 0.0))
    return out, use


def counts(rows, preds, threshold, lang, bucket=None):
    """(true positives, predicted, support) for *lang* at *threshold*."""
    tp = predicted = support = 0
    for (truth, text, _a), (guess, conf) in zip(rows, preds, strict=True):
        if bucket and _bucket(text) != bucket:
            continue
        support += truth == lang
        if conf >= threshold and guess == lang:
            predicted += 1
            tp += truth == lang
    return tp, predicted, support


def score(rows, preds, threshold, lang, bucket=None):
    """(precision, recall, support, predicted, Wilson lower bound)."""
    tp, predicted, support = counts(rows, preds, threshold, lang, bucket)
    return (tp / predicted if predicted else None,
            tp / support if support else None, support, predicted,
            wilson_lower(tp, predicted))


def coverage(rows, preds, threshold, bucket=None):
    total = judged = 0
    for (_t, text, _a), (_g, conf) in zip(rows, preds, strict=True):
        if bucket and _bucket(text) != bucket:
            continue
        total += 1
        judged += conf >= threshold
    return judged / total if total else None, total


def _lowers(rows, preds, threshold, bucket=None):
    """The Wilson lower bound per target, or None when a target has fewer
    than MIN_PREDICTED judged posts."""
    lowers = []
    for lang in TARGETS:
        tp, predicted, _s = counts(rows, preds, threshold, lang, bucket)
        if predicted < MIN_PREDICTED:
            return None
        lowers.append(wilson_lower(tp, predicted))
    return lowers


def pick_threshold(rows, preds, bucket=None):
    """(smallest threshold whose worst-target Wilson lower bound reaches
    the goal, threshold with the best worst-target lower bound). Either is
    None when no threshold has MIN_PREDICTED judged posts per target."""
    first = best = None
    best_value = -1.0
    for step in range(50, 100):
        threshold = step / 100
        lowers = _lowers(rows, preds, threshold, bucket)
        if lowers is None:
            continue
        worst = min(lowers)
        if first is None and worst >= PRECISION_GOAL:
            first = threshold
        if worst > best_value:
            best, best_value = threshold, worst
    return first, best


def _fmt(value):
    return '  n/a' if value is None else '%.3f' % value


def report(name, rows, preds, used):
    print('== %s; %d languages: %s' % (name, len(used), ' '.join(used)))
    scopes = [(None, 'all lengths')] + [(b[0], 'length %s' % b[0])
                                        for b in BUCKETS]
    for bucket, label in scopes:
        _cov, total = coverage(rows, preds, 0.5, bucket)
        print('-- %s: %d posts' % (label, total))
        for threshold in TABLE_THRESHOLDS:
            cov, _t = coverage(rows, preds, threshold, bucket)
            cells = []
            for lang in TARGETS:
                p, r, s, n, lo = score(rows, preds, threshold, lang, bucket)
                cells.append('%s prec %s rec %s wilson %.3f supp %d pred %d'
                             % (lang, _fmt(p), _fmt(r), lo, s, n))
            print('   thr %.2f cover %s | %s' % (threshold, _fmt(cov),
                                                 ' | '.join(cells)))
        first, best = pick_threshold(rows, preds, bucket)
        print('   smallest threshold with Wilson lower bound >= %.2f and '
              'at least %d judged per language: %s'
              % (PRECISION_GOAL, MIN_PREDICTED,
                 'none' if first is None else '%.2f' % first))
        if best is None:
            print('   best worst-case lower bound: no threshold has enough '
                  'judged posts')
        else:
            lowers = _lowers(rows, preds, best, bucket)
            cov, _t = coverage(rows, preds, best, bucket)
            print('   best worst-case Wilson lower bound: %.3f at %.2f '
                  '(coverage %s; en %.3f, pt %.3f)'
                  % (min(lowers), best, _fmt(cov), lowers[0], lowers[1]))


def holdout(rows, preds):
    """Pick on one author half, report on the other, both directions."""
    halves = ([], [])
    for row, pred in zip(rows, preds, strict=True):
        halves[half_of(row[2])].append((row, pred))
    print('-- hold-out by author: halves of %d and %d posts'
          % (len(halves[0]), len(halves[1])))
    for pick_on, check_on in ((0, 1), (1, 0)):
        train_rows = [r for r, _p in halves[pick_on]]
        train_preds = [p for _r, p in halves[pick_on]]
        test_rows = [r for r, _p in halves[check_on]]
        test_preds = [p for _r, p in halves[check_on]]
        first, best = pick_threshold(train_rows, train_preds)
        for label, threshold in (('goal', first), ('best', best)):
            if threshold is None:
                print('   pick on half %d (%s): no threshold' % (pick_on,
                                                                 label))
                continue
            cov, _t = coverage(test_rows, test_preds, threshold)
            cells = []
            for lang in TARGETS:
                p, r, s, n, lo = score(test_rows, test_preds, threshold,
                                       lang)
                cells.append('%s prec %s wilson %.3f supp %d pred %d'
                             % (lang, _fmt(p), lo, s, n))
            print('   pick on half %d (%s) thr %.2f -> half %d cover %s | %s'
                  % (pick_on, label, threshold, check_on, _fmt(cov),
                     ' | '.join(cells)))


def langbench(paths, with_simplemma=False):
    try:
        import numpy  # noqa: F401
        import py3langid  # noqa: F401
    except ImportError as error:
        print('langbench needs numpy and py3langid: %s' % error)
        return 2
    rows, stats = labelled_posts(paths)
    print('logs %(logs)d (%(unreadable)d skipped: no readable schema-2 '
          'record), posts read %(posts)d, repeated texts skipped '
          '%(duplicate)d, no declared language %(no_lang)d, no verified '
          'text %(no_text)d' % stats)
    tally = {}
    for row in rows:
        tally[row[0]] = tally.get(row[0], 0) + 1
    print('labelled: %d posts; declared languages: %s' % (
        len(rows), ' '.join('%s=%d' % item for item in
                            sorted(tally.items(), key=lambda i: -i[1]))))
    if not rows:
        return 1
    unseen = sorted(set(tally) - set(RUNTIME_LANGS))
    print('declared languages missing from RUNTIME_LANGS: %s'
          % (' '.join(unseen) or 'none'))
    for label, langs in (('RUNTIME_LANGS', RUNTIME_LANGS),
                         ('COMMON', COMMON)):
        preds, used = predict_langid(rows, langs)
        report('py3langid 0.4.0, norm_probs=True, %s' % label, rows, preds,
               used)
        if label == 'RUNTIME_LANGS':
            holdout(rows, preds)
    if with_simplemma:
        try:
            preds, used = predict_simplemma(rows, RUNTIME_LANGS)
        except ImportError:
            print('simplemma is not importable; skipped')
        else:
            report('simplemma langdetect (score = share of known words)',
                   rows, preds, used)
    return 0


if __name__ == '__main__':
    sys.exit(langbench(None))
