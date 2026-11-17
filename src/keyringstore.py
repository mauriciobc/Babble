# SPDX-License-Identifier: GPL-3.0-or-later
"""Secret Service storage: the one place secrets reach the keyring.

Tokens and client credentials are asymmetric blobs, so they live behind
one schema and one serialized service lookup; the transport in
``mastodon`` and the OAuth calls in ``clientoauth`` call in here instead
of each opening its own ``Secret.Service``.
"""

import gi

gi.require_version('Secret', '1')

from gi.repository import Gio, GLib, GObject, Secret  # noqa: E402
SECRET_SCHEMA = Secret.Schema.new('io.github.mauriciobc.Babble',
                                  Secret.SchemaFlags.NONE,
                                  {
                                      'instance': Secret.SchemaAttributeType.STRING,
                                      'kind': Secret.SchemaAttributeType.STRING,
                                  })

# Lazily built by _with_secret_service(); see there for why one proxy is
# shared rather than created per call. Waiters coalesce on the in-flight
# Secret.Service.get the way instance_info_async coalesces.
_service = None

_service_waiters = []

_service_getting = False

def _with_secret_service(callback):
    """Run *callback(service, error)* once the session keyring proxy is
    ready, without blocking the main loop.

    Concurrent callers share one ``Secret.Service.get``. The proxy is
    reused afterwards: async store/clear must finish on the same
    proxy that started them. (Reads no longer use it; they run on a
    worker thread, see :func:`load_secret_async`.) A failed get is not
    remembered as a failure: the keyring can be unlocked a moment later,
    so the next call is allowed to find it.
    """
    global _service_getting
    if _service is not None:
        callback(_service, None)
        return
    _service_waiters.append(callback)
    if _service_getting:
        return
    _service_getting = True

    def dispatch(service, error):
        global _service
        if service is not None:
            _service = service
        waiters, _service_waiters[:] = list(_service_waiters), []
        for waiter in waiters:
            waiter(service, error)

    def done(_source, result, _user_data=None):
        global _service_getting
        _service_getting = False
        try:
            service = Secret.Service.get_finish(result)
        except GLib.Error as failure:
            dispatch(None, failure)
            return
        dispatch(service, None)

    try:
        Secret.Service.get(Secret.ServiceFlags.NONE, None, done, None)
    except Exception as failure:  # arming the call is all that can fail here
        # A waiter list nobody empties would hang every later caller, so
        # a synchronous failure has to reach them as a keyring error.
        _service_getting = False
        dispatch(None, GLib.Error(
            str(failure), 'mastodon-keyring-error-quark', 0))

def store_secret_async(instance, kind, payload, callback=None,
                       cancellable=None):
    """Write *payload* to the keyring without blocking the main loop.

    *callback* receives None once the entry is stored, or the GLib.Error
    that stopped it. A keyring that will not take the secret is not a
    transport failure, but the caller has to learn about it: an account
    recorded without a recoverable token is an account that silently
    signs the user out on the next start.
    """

    def ready(service, error):
        if error is not None:
            if callback is not None:
                callback(error)
            return

        def done(_service, result, _user_data):
            failure = None
            try:
                service.store_finish(result)
            except GLib.Error as err:
                failure = err
            if callback is not None:
                callback(failure)

        service.store(SECRET_SCHEMA,
                      {'instance': instance, 'kind': kind},
                      Secret.COLLECTION_DEFAULT,
                      'Babble: %s' % instance,
                      Secret.Value.new(payload, len(payload),
                                        'text/plain'),
                      cancellable, done, None)

    _with_secret_service(ready)

def load_secret_async(instance, kind, callback, cancellable=None):
    """Read a keyring entry without blocking the main loop.

    *callback* is ``callback(value, error=None)``, called on the main
    loop. ``value`` is the secret text, or None when the entry is absent.
    A keyring that cannot be read (locked, missing service, D-Bus
    failure) is not an absence: *error* is the ``GLib.Error`` and *value*
    is None. Every callback takes both arguments; the stubs in the tests
    were updated with it.

    The read runs on a worker thread. Done as a chain of async D-Bus
    calls on the main loop, it needs one loop iteration per step (the
    service, its session, the lookup), and at launch each of those waited
    behind a first-frame redraw: the token, and with it the first request,
    arrived 130-580 ms after the window was up, for a lookup that takes
    7 ms. A thread finishes while the window is still being built and
    hands the result over in a single dispatch.
    """
    attributes = {'instance': instance, 'kind': kind}

    def work(task, _source, _data, token):
        # Worker thread: the keyring only, no widget or GSettings access.
        try:
            secret = Secret.password_lookup_sync(SECRET_SCHEMA, attributes,
                                                 token)
        except GLib.Error as failure:
            task.return_error(failure)
            return
        task.return_value(GObject.Value(GObject.TYPE_PYOBJECT, (secret,)))

    def done(_source, task, _data):
        try:
            _ok, (secret,) = task.propagate_value()
        except GLib.Error as failure:
            callback(None, failure)
            return
        callback(secret, None)

    task = Gio.Task.new(None, cancellable, done, None)
    task.run_in_thread(work)

def clear_secret_async(instance, kind, callback=None, cancellable=None):
    """Remove a keyring entry without blocking the main loop.

    *callback* receives None, or the GLib.Error that stopped it. An
    entry that will not delete leaves a live token behind, so the caller
    is told rather than left to assume the removal took.
    """

    def ready(service, error):
        if error is not None:
            if callback is not None:
                callback(error)
            return

        def done(_service, result, _user_data):
            failure = None
            try:
                service.clear_finish(result)
            except GLib.Error as err:
                failure = err
            if callback is not None:
                callback(failure)

        service.clear(SECRET_SCHEMA,
                      {'instance': instance, 'kind': kind},
                      cancellable, done, None)

    _with_secret_service(ready)
