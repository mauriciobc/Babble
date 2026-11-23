# SPDX-License-Identifier: GPL-3.0-or-later
"""TF-IDF vectors, their cosine, and the relevance of posts to a focus.

The formulas are Java's (`TFIDFCalculator`, `FeedChefUtils`,
`RelevanceComparator`): the weight of a term is the augmented term
frequency times the smoothed inverse document frequency
(`textutil.tfidf_vector`), a vector keeps at most `MAX_TERMS` terms, and
the similarity of two vectors is their cosine. Synonyms and trigrams
(Java's fuzzy matching) stay off, as in the rest of Feed Chef.

A "More Like This" focus is a reference vector built from the recipe's
keywords and their weights (a weight that is missing, or not above zero,
counts as in Java: all equal, or 0.1). The relevance of a post is the cosine
of its vector with that reference; the kitchen orders posts by it, newest
first and then by id among equals, as for any other score.

Everything here is plain Python on purpose. The numbers feed an order that
a replay must reproduce bit for bit, and a sum computed by a BLAS routine
need not round like a sum in a loop; at the size of a page (tens of posts)
the loop costs a few milliseconds, which is measured in
`src/test-feedchef-relevance.py`. Sums run over sorted terms, so the
result does not depend on dictionary order. No GTK, no third-party module.
"""

from __future__ import annotations

import math
from typing import Any

from . import textutil
from .textutil import fold_text, text_tokens, tfidf_vector

# Java's MAX_VECTOR_SIZE: a post with more distinct terms keeps its best.
MAX_TERMS = 100
# What a focus weight that is not above zero is read as (Java).
MIN_WEIGHT = 0.1


class WordCache:
    """The focus text and the words of each post, read once for one cook:
    the relevance sort, the grouping and the audit all ask for them, and
    reading a post's HTML is the dearest part. It holds the posts it was
    asked about until it is dropped (with the cook)."""

    def __init__(self):
        self._text: dict[int, tuple[dict, str]] = {}
        self._words: dict[int, tuple[dict, list[str]]] = {}

    def text(self, status: dict[str, Any]) -> str:
        found = self._text.get(id(status))
        if found is not None and found[0] is status:
            return found[1]
        # Through the module, so that a replay's stand-in is the one used.
        text = textutil.focus_text(status)
        self._text[id(status)] = (status, text)
        return text

    def words(self, status: dict[str, Any]) -> list[str]:
        found = self._words.get(id(status))
        if found is not None and found[0] is status:
            return found[1]
        words = text_tokens(self.text(status))
        self._words[id(status)] = (status, words)
        return words


class Vector:
    """A sparse vector: its terms in sorted order, and its Euclidean norm."""

    __slots__ = ('items', 'terms', 'norm')

    def __init__(self, terms: dict[str, float], limit: int | None = None):
        if limit is not None and len(terms) > limit:
            best = sorted(terms.items(), key=lambda kv: (-kv[1], kv[0]))
            terms = dict(best[:limit])
        self.items = tuple(sorted(terms.items()))
        self.terms = dict(self.items)
        total = 0.0
        for _term, weight in self.items:
            total += weight * weight
        self.norm = math.sqrt(total)


def vectors_of(statuses: list[dict[str, Any]],
               cache: WordCache | None = None) -> list[Vector]:
    """The TF-IDF vector of each post, parallel to *statuses*. The corpus
    is the list itself: a document frequency counts the posts of it, and a
    post with no words still counts as a document (as in Java). A boost
    is read as the post it boosts."""
    cache = cache or WordCache()
    token_lists = [cache.words(status) for status in statuses]
    df: dict[str, int] = {}
    for tokens in token_lists:
        for token in set(tokens):
            df[token] = df.get(token, 0) + 1
    total = len(statuses)
    return [Vector(tfidf_vector(tokens, df, total), MAX_TERMS)
            for tokens in token_lists]


def cosine(first: Vector, second: Vector) -> float:
    """Java's `calculateCosineSimilarity`: 0.0 when either vector is
    empty, otherwise the dot product over the terms they share divided by
    the product of the norms. Symmetric to the last bit."""
    if first.norm == 0 or second.norm == 0:
        return 0.0
    small, large = ((first, second) if len(first.items) < len(second.items)
                    else (second, first))
    other = large.terms
    dot = 0.0
    for term, weight in small.items:
        found = other.get(term)
        if found is not None:
            dot += weight * found
    return dot / (first.norm * second.norm)


def usable_weights(keywords: list[str], weights: list[float] | None
                   ) -> list[float]:
    """The weight of each keyword as the reference vector reads it: all
    1.0 when the weights are not given for every keyword, else each
    weight, or 0.1 when it is not a number above zero."""
    if not weights or len(weights) != len(keywords):
        return [1.0] * len(keywords)
    out = []
    for weight in weights:
        try:
            value = float(weight)
        except (TypeError, ValueError):
            value = 0.0
        out.append(value if math.isfinite(value) and value > 0
                   else MIN_WEIGHT)
    return out


def keyword_of(word: Any) -> str:
    """A focus keyword in the form posts are tokenized in: lowercase, no
    accents (an imported recipe may carry another form)."""
    return fold_text(str(word or '')).strip()


def focus_keywords(kitchen) -> list[str]:
    """The keywords of a recipe's focus that say anything: the ones the
    relevance sort works with (none: there is no focus to sort by)."""
    return [word for word in kitchen.active_focus_keywords or []
            if keyword_of(word)]


def reference_vector(keywords: list[str] | None,
                     weights: list[float] | None = None) -> Vector:
    """Java's `createReferenceVectorFromKeywords`: one term per keyword
    with its weight (a keyword written twice keeps the last weight)."""
    words = [keyword_of(word) for word in keywords or []]
    given = usable_weights(list(keywords or []), weights)
    terms = {}
    for word, weight in zip(words, given, strict=True):
        if word:
            terms[word] = weight
    # Only the proportions matter to a cosine, so the weights are put on a
    # scale where the largest is 1: a focus whose weights all faded by the
    # same factor then gives the very same vector (``focus.decay_focus``).
    top = max(terms.values(), default=0.0)
    if top > 0:
        terms = {word: float('%.12g' % (weight / top))
                 for word, weight in terms.items()}
    return Vector(terms)


def relevance_scores(statuses: list[dict[str, Any]],
                     keywords: list[str] | None,
                     weights: list[float] | None = None,
                     cache: WordCache | None = None
                     ) -> dict[str, float]:
    """The relevance of each post, by the id of its timeline entry: the
    cosine of its vector with the reference vector of the focus."""
    reference = reference_vector(keywords, weights)
    scores: dict[str, float] = {}
    for status, vector in zip(statuses, vectors_of(statuses, cache),
                              strict=True):
        # Rounded to 12 significant digits: two posts that are equally
        # relevant must tie (and fall to the tie rule) instead of being
        # told apart by the last bits of a sum.
        scores[str(status.get('id') or '')] = float(
            '%.12g' % cosine(reference, vector))
    return scores


def matched(scores: dict[str, float]) -> bool:
    """Whether any post has some relevance to the focus."""
    return any(score > 0 for score in scores.values())
