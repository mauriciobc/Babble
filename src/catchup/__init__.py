# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up, the separate view over the people you follow."""


def __getattr__(name):
    # Importing a submodule runs this file, and `accounts` imports `store`
    # at start-up; nothing here may load the pipeline until it is asked for.
    if name == 'run':
        from .pipeline import run
        return run
    if name == 'CatchUpPage':
        from .page import CatchUpPage
        return CatchUpPage
    if name == 'ScoredCandidate':
        from .rank_model import ScoredCandidate
        return ScoredCandidate
    raise AttributeError(name)


__all__ = ['CatchUpPage', 'ScoredCandidate', 'run']


