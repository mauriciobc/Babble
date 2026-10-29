# SPDX-License-Identifier: GPL-3.0-or-later
"""Catch Up, the separate view over the people you follow."""

def __getattr__(name):
    if name != 'CatchUpPage':
        raise AttributeError(name)
    from .page import CatchUpPage
    return CatchUpPage

__all__ = ['CatchUpPage']
