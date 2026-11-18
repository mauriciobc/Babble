# SPDX-License-Identifier: GPL-3.0-or-later
"""Background work: streaming, the background hold and the shortcut.

The window's lifecycle half: starting and syncing the stream, routing
connection and event changes, the close request that asks for the
background hold, the periodic background status, and the global shortcut.
A mixin of ``BabbleWindow``.
"""
import gi

from .i18n import gettext as _, ngettext

gi.require_version('Gtk', '4.0')

from gi.repository import GLib, Gtk  # noqa: E402

from . import feedback  # noqa: E402
from . import portals  # noqa: E402
from .timelinepage import TimelinePage  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402



class WindowBackground:
    def _setup_streaming(self):
        self.notifications_badge = Gtk.Label(visible=False)
        self.notifications_badge.add_css_class('notifications-badge')
        self.notifications_badge.add_css_class('caption-heading')
        self.notifications_badge.add_css_class('numeric')

        from .desktopnotify import DesktopNotifier
        from .streaming import StreamingManager
        from .streamrouter import StreamHost, StreamRouter
        self.notifier = DesktopNotifier(self)
        self.streaming = StreamingManager()
        # Which streams the open places want, and who receives each
        # event. The router reaches pages through these callables, at
        # each event, and holds none of them.
        nav = self.navigation
        self.stream_router = StreamRouter(StreamHost(
            home_page=lambda: self.timeline_page,
            notifications_page=lambda: self.notifications_page,
            place=lambda tag: nav.children.get(tag),
            stream_places=nav.stream_places,
            place_timelines=nav.place_timelines,
            is_timeline=lambda page: isinstance(page, TimelinePage),
            live_public=lambda: self.settings.get_boolean(
                'public-live-updates'),
            refresh_admin_tool=lambda tool: self._refresh_admin_tool(tool),
            notifier_handle=lambda payload: self.notifier.handle(payload),
            note_background_notification=(
                lambda payload: self.note_background_notification(payload)),
        ))
        connect_weak(self.streaming, 'event', self._on_stream_event)
        connect_weak(self.streaming, 'connection-changed',
                           self._on_stream_connection_changed)
        # The stream went down after it had been up: whatever was posted
        # meanwhile never came through it.
        self._stream_dropped = False
        # The open admin dashboard, so a streaming report can refresh it.
        self._admin_dialog = None

    def _sync_streaming(self):
        if self.client is None or not self.client.token:
            return
        if not self.settings.get_boolean('live-updates'):
            self.streaming.stop()
            self.notifier.start_polling()
            return
        self.streaming.set_streams(self.stream_router.wanted_streams())
        if not self.streaming.connected:
            self.streaming.start(self.client)
            # Many instances sit behind CDNs that reject the upgrade;
            # poll until the socket actually opens.
            self.notifier.start_polling()

    def _on_stream_connection_changed(self, _manager, connected):
        if connected:
            self.notifier.stop_polling()
            if self._stream_dropped:
                self._catch_up_after_outage()
            self._stream_dropped = False
        elif self.client is not None:
            self._stream_dropped = True
            self.notifier.start_polling()
        self._schedule_background_status()

    def _catch_up_after_outage(self):
        """Fetch what was posted while the stream was down.

        The stream does not replay. Which built places refresh comes from
        :func:`navigation.place_policy`; this method only decides when.
        Dedupes by ``id(page)`` so unhashable test doubles still skip a
        second refresh. Timeline pages use the stream host's
        ``is_timeline`` check; conversations (and any other non-timeline
        with ``catch_up='refresh'``) call ``refresh`` directly.
        """
        from .navigation import place_policy
        seen = set()
        home = self.timeline_page
        if home is not None:
            home.refresh()
            seen.add(id(home))
        if self.notifications_page is not None:
            self.notifications_page.catch_up()
        is_timeline = self.stream_router.host.is_timeline
        for tag, child in self.navigation.children.items():
            if place_policy(tag).catch_up != 'refresh':
                continue
            page = getattr(child, 'timeline', child)
            if page is None or id(page) in seen:
                continue
            seen.add(id(page))
            if is_timeline(page):
                # A place never loaded has nothing to catch up on.
                if not page.has_loaded():
                    continue
                page.refresh()
                continue
            refresh = getattr(page, 'refresh', None)
            if refresh is not None:
                refresh()

    def _on_stream_event(self, _manager, stream, name, payload):
        # Which page receives which event is the router's knowledge; the
        # window only hands the event over (and is what the manager's
        # signal is connected to, weakly).
        self.stream_router.route(stream, name, payload)

    def _on_close_request(self, _window):
        """With background mode on, closing hides the window and keeps
        the application (and its notifications) running."""
        self._cancel_sidebar_visibility()
        if self.client is None or not self.settings.get_boolean(
                'work-in-background'):
            # A hold left from an earlier hide would outlive the window.
            self._release_hold()
            feedback.flush_deferred()
            return False
        app = self.get_application()
        if app is not None and not self._held:
            app.hold()
            self._held = True
        self.set_visible(False)
        if not self._background_asked:
            self.request_background()
        return True

    def request_background(self):
        """Ask the XDG Background portal to let the app run with its
        window closed. A refusal turns ``work-in-background`` off and
        says so; no portal (unsandboxed host, D-Bus error) is silently
        ignored. Asked at most once at a time; fully async."""
        if self._background_pending:
            return
        self._background_pending = True
        self._background_asked = True
        ref = self.weak_ref()
        portals.session_bus(
            lambda bus: portals.request_background(ref, bus))

    def _background_done(self, allowed):
        self._background_pending = False
        self._background_allowed = allowed
        if allowed:
            self._schedule_background_status()
            return
        self.settings.set_boolean('work-in-background', False)
        feedback.show_toast(self, _('Running in the background was not allowed'))
        # Released even when the window was shown again meanwhile, so
        # its next close is not kept alive by a stale hold.
        self._release_hold()
        if not self.get_visible():
            # Hidden by a close the portal now refuses: finish closing.
            self.close()

    def _background_failed(self):
        # No portal or a D-Bus error: nothing refused, so stay in the
        # background, but ask again on the next close in case the
        # failure was transient.
        self._background_pending = False
        self._background_asked = False

    def _release_hold(self):
        app = self.get_application()
        if app is not None and self._held:
            app.release()
            self._held = False

    def note_background_notification(self, notification):
        """Count a mention that arrived while the window is hidden, for
        the background status line. Any notification may be passed, the
        same one twice included."""
        if self.get_visible() or not isinstance(notification, dict) \
                or notification.get('type') != 'mention':
            return
        nid = str(notification.get('id') or '')
        if not nid or nid in self._background_mentions:
            return
        self._background_mentions.add(nid)
        self._schedule_background_status()

    def _on_visible_changed(self, _window, _pspec):
        if not self.get_visible():
            self._schedule_background_status()
            return
        self._background_mentions.clear()
        if self._status_source:
            GLib.source_remove(self._status_source)
            self._status_source = 0
        if self._background_status:
            self._background_status = ''
            portals.set_background_status('')

    def _schedule_background_status(self):
        """Refresh the line GNOME Shell shows for the app running in the
        background, once the changes of the next second have landed."""
        if not self._background_allowed or self.get_visible() \
                or self._status_source:
            return
        self._status_source = GLib.timeout_add(
            portals.STATUS_DELAY_MS,
            weak_method(self._flush_background_status))

    def _flush_background_status(self):
        self._status_source = 0
        if self._background_allowed and not self.get_visible():
            message = self._background_status_text()[
                :portals.STATUS_MAX_CHARS]
            if message != self._background_status:
                self._background_status = message
                portals.set_background_status(message)
        return GLib.SOURCE_REMOVE

    def _background_status_text(self):
        count = len(self._background_mentions)
        if count:
            # Translators: status GNOME Shell shows for Babble running in
            # the background with its window closed.
            return ngettext('%d new mention', '%d new mentions',
                            count) % count
        if self.streaming.connected:
            # Translators: background status: live updates are streaming.
            return _('Connected')
        # Translators: background status: no live stream, so Babble polls
        # the server for new notifications.
        return _('Checking for notifications')

    def _sync_global_shortcut(self):
        """Bind the quick post shortcut through the GlobalShortcuts portal
        while ``global-shortcut`` is on; close the session once it is
        off. A missing portal is silently ignored."""
        if not self.settings.get_boolean('global-shortcut'):
            if self._shortcut_session is not None:
                self._shortcut_session.close()
                self._shortcut_session = None
            return
        if self._shortcut_session is not None or self._shortcut_pending:
            return
        self._shortcut_pending = True
        ref = self.weak_ref()
        portals.session_bus(
            lambda bus: portals.create_shortcut_session(ref, bus))

    def _shortcut_ready(self, session, refused):
        self._shortcut_pending = False
        if refused:
            self.settings.set_boolean('global-shortcut', False)
            feedback.show_toast(self,
                             _('The quick post shortcut was not allowed'))
            return
        if session is None:
            return
        if not self.settings.get_boolean('global-shortcut'):
            session.close()  # switched off while it was being bound
            return
        self._shortcut_session = session
