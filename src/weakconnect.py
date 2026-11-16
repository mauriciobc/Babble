# SPDX-License-Identifier: GPL-3.0-or-later

"""Signal handlers and callbacks that hold a GObject weakly.

A widget that connects a signal of something it owns to one of its own
methods must use :func:`connect_weak`, and one that hands a method to a
child it owns as a callback :func:`weak_method`: a plain connect forms a
reference cycle through C that Python's collector cannot see. Nothing
here imports GTK, so it can sit under every other module.
"""


def connect_weak(emitter, signal, method, *extra):
    """``emitter.connect(signal, method, *extra)`` holding the bound
    *method*'s object weakly.

    A widget that connects a signal of something it owns (a child
    widget, an event controller, an adjustment, an action) to one of its
    own methods closes a reference cycle through C that Python's
    collector cannot see: the handler keeps the widget's wrapper, the
    wrapper keeps the GObject, the GObject keeps the emitter. The widget
    and its whole subtree then outlive every list row, dialog or page
    that dropped it. With the object held weakly the cycle never forms;
    once it is finalized the handler disconnects itself.

    The reference is a GObject weak reference, not a Python one: PyGObject
    drops the Python wrapper of an object only GTK still holds and builds
    a new one on demand, so a ``weakref`` to the wrapper dies while the
    widget lives on.
    """
    func = method.__func__
    target = method.__self__.weak_ref()

    def handler(source, *args):
        owner = target()
        if owner is None:
            source.disconnect_by_func(handler)
            return False
        return func(owner, source, *args)

    return emitter.connect(signal, handler, *extra)


def weak_method(method):
    """Callable forwarding to *method*, a GObject's bound method, without
    keeping the object alive; a no-op once it is finalized. For callbacks
    a widget hands to a child it owns (as a Python attribute, a list-row
    factory...), which a strong reference would turn into a cycle through
    C. A GObject weak reference, as in :func:`connect_weak`."""
    func = method.__func__
    target = method.__self__.weak_ref()

    def call(*args):
        owner = target()
        return None if owner is None else func(owner, *args)
    return call
