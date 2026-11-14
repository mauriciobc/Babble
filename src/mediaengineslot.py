# SPDX-License-Identifier: GPL-3.0-or-later
"""One engine slot shared by the media viewer and inline GIFV player.

Creates the :class:`mediaengine.Engine` lazily, builds the
:class:`mediastream.EngineStream` before ``load``, stops without
releasing on navigation, and releases once on close. GStreamer is
imported only when :meth:`EngineSlot.start` runs.
"""


class PlaybackUnavailable(Exception):
    """GStreamer is missing or the engine refused to start."""


class EngineSlot:
    """Owns at most one engine; callers keep the stream and the widgets."""

    def __init__(self):
        self._engine = None

    @property
    def engine(self):
        return self._engine

    def start(self, url, *, user_agent, gl):
        """Return a new stream for *url*, creating the engine if needed.

        The stream is constructed before ``engine.load(url)``. On
        ``ImportError``, ``ValueError`` or ``RuntimeError``
        :class:`PlaybackUnavailable` is raised and the slot keeps whatever
        engine it has: whether to release it is the caller's decision, as
        it was before the slot (the inline player releases; the viewer
        keeps its engine for the next attachment and releases on close).
        """
        try:
            from . import mediaengine
            from .mediastream import EngineStream
            if self._engine is None:
                self._engine = mediaengine.Engine(
                    user_agent=user_agent, gl=gl)
            stream = EngineStream(self._engine)
            self._engine.load(url)
        except (ImportError, ValueError, RuntimeError) as error:
            raise PlaybackUnavailable(str(error)) from error
        return stream

    def stop(self, stream=None):
        """Pause and detach *stream* (when given) and stop the engine.

        The engine is kept for the next :meth:`start`.
        """
        if stream is not None:
            stream.pause()
            stream.detach()
        if self._engine is not None:
            self._engine.stop()

    def release(self):
        """Hand the engine to its deferred release and clear the slot."""
        engine, self._engine = self._engine, None
        if engine is not None:
            engine.release()
