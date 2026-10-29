# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up, the separate view over the people you follow."""

from .pipeline import run


def __getattr__(name):
    if name == 'CatchUpPage':
        from .page import CatchUpPage
        return CatchUpPage
    if name == 'ScoredCandidate':
        from .rank_model import ScoredCandidate
        return ScoredCandidate
    raise AttributeError(name)


__all__ = ['CatchUpPage', 'ScoredCandidate', 'run']


