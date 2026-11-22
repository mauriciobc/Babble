# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread API adapters that build a FeedEngine for a Babble session."""

from __future__ import annotations

import threading

from gi.repository import GLib

from . import appsettings
from .api import accounts as api_accounts
from .api import core as api_core
from .api import search as api_search
from .api import tags as api_tags
from .api import timelines as api_timelines
from .api import trends as api_trends
from .feedchef.engine import FeedEngine
from .feedchef.kitchen import ServerTrendContext


def _wait(start, finish, client, timeout=60):
    """Run a Gio.Task start/finish pair and wait.

    On the main thread, pump the default context (tests / sync callers).
    On a worker, schedule the start with ``idle_add`` and wait on an Event
    so the UI thread alone iterates the default MainContext.
    """
    box = {'done': False, 'value': None, 'error': None}
    done = threading.Event()

    def callback(_source, result, *_user_data):
        try:
            box['value'] = finish(client, result)
        except Exception as error:
            box['error'] = error
        box['done'] = True
        done.set()

    def kickoff():
        start(client, callback=callback)
        return GLib.SOURCE_REMOVE

    main = threading.current_thread() is threading.main_thread()
    if main:
        kickoff()
        context = GLib.MainContext.default()
        expired = []
        # A real deadline: iteration(True) blocks while nothing is pending,
        # so counting iterations is no time limit.
        guard = GLib.timeout_add(int(timeout * 1000),
                                 lambda: expired.append(True) or False)
        while not box['done'] and not expired:
            context.iteration(True)
        if not expired:
            GLib.source_remove(guard)
    else:
        GLib.idle_add(kickoff)
        if not done.wait(timeout):
            raise TimeoutError('feedchef API wait timed out')
    if not box['done']:
        raise TimeoutError('feedchef API wait timed out')
    if box['error'] is not None:
        raise box['error']
    return box['value']


def _params(max_id, count):
    params = {'limit': str(count)}
    if max_id:
        params['max_id'] = max_id
    return params


def fetch_scope_for(client, scope, list_id, max_id, count):
    params = _params(max_id, count)
    if scope == 'local':
        params = dict(params, local='true')

        def start(c, callback=None, cancellable=None):
            return api_timelines.public_async(
                c, params=params, callback=callback, cancellable=cancellable)

        finish = api_timelines.public_finish
    elif scope == 'federated':
        def start(c, callback=None, cancellable=None):
            return api_timelines.public_async(
                c, params=params, callback=callback, cancellable=cancellable)

        finish = api_timelines.public_finish
    elif scope == 'list' and list_id:
        def start(c, callback=None, cancellable=None):
            return api_timelines.list_async(
                c, list_id, params=params, callback=callback,
                cancellable=cancellable)

        finish = api_timelines.list_finish
    else:
        def start(c, callback=None, cancellable=None):
            return api_timelines.home_async(
                c, params=params, callback=callback, cancellable=cancellable)

        finish = api_timelines.home_finish
    data = _wait(start, finish, client)
    return data if isinstance(data, list) else []


def make_engine(client, account_key, account_id, recipe) -> FeedEngine:
    scope = recipe.pantry.scope
    list_id = recipe.pantry.list_id

    def fetch_scope(max_id, count):
        return fetch_scope_for(client, scope, list_id, max_id, count)

    def fetch_hashtag(tag, count):
        params = _params(None, count)

        def start(c, callback=None, cancellable=None):
            return api_timelines.tag_async(
                c, tag, params=params, callback=callback,
                cancellable=cancellable)

        data = _wait(start, api_timelines.tag_finish, client)
        return data if isinstance(data, list) else []

    def search_statuses(query):
        def start(c, callback=None, cancellable=None):
            return api_search.search_async(
                c, query, type='statuses', limit=20,
                callback=callback, cancellable=cancellable)

        def finish(c, result):
            payload = api_search.search_finish(c, result)
            if isinstance(payload, dict):
                return payload.get('statuses') or []
            return []

        return _wait(start, finish, client)

    def fetch_trends():
        tags = {}
        status_ids = set()
        try:
            def start_tags(c, callback=None, cancellable=None):
                return api_tags.trending_async(
                    c, params={'limit': '20'}, callback=callback,
                    cancellable=cancellable)

            raw_tags = _wait(start_tags, api_core.finish, client)
            for item in raw_tags or []:
                name = str((item or {}).get('name') or '').lower()
                if name:
                    tags[name] = 1.0
        except Exception:
            pass
        try:
            def start_st(c, callback=None, cancellable=None):
                return api_trends.statuses_async(
                    c, params={'limit': '20'}, callback=callback,
                    cancellable=cancellable)

            raw = _wait(start_st, api_trends.statuses_finish, client)
            for item in raw or []:
                sid = str((item or {}).get('id') or '')
                if sid:
                    status_ids.add(sid)
        except Exception:
            pass
        return ServerTrendContext(trending_hashtags=tags,
                                  trending_status_ids=status_ids)

    def fetch_following_page(max_id):
        params = {'limit': '80'}
        if max_id:
            params['max_id'] = max_id

        def start(c, callback=None, cancellable=None):
            return api_accounts.following_async(
                c, account_id, params=params, callback=callback,
                cancellable=cancellable)

        def finish(c, result):
            try:
                data, next_params, _prev = api_core.finish_paged(c, result)
            except Exception:
                data = api_core.finish(c, result)
                next_params = None
            next_max = None
            if isinstance(next_params, dict):
                next_max = next_params.get('max_id')
            return data or [], next_max

        return _wait(start, finish, client)

    return FeedEngine(
        account_key=account_key,
        self_account_id=account_id,
        fetch_scope=fetch_scope,
        fetch_hashtag=fetch_hashtag,
        search_statuses=search_statuses,
        fetch_trends=fetch_trends,
        fetch_following_page=fetch_following_page,
        ui_idle=lambda cb: GLib.idle_add(cb),
        native_bluesky=lambda: appsettings.settings().get_boolean(
            'bluesky-native'),
    )
