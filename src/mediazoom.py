# SPDX-License-Identifier: GPL-3.0-or-later

"""Zoom and pan for a full-size image attachment.

:class:`ZoomPicture` is the texture view the media viewer builds for
image attachments. Playback (GstPlay / ``Gtk.MediaFile``) lives in
:mod:`mediaplayback`; the viewer chrome that constructs this widget
stays in :mod:`mediaviewer`.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')

from gi.repository import (  # noqa: E402
    Gdk, GObject, Graphene, Gsk, Gtk,
)

from . import pages  # noqa: E402

ZOOM_STEP = 1.25
MAX_SCALE = 8.0


class ZoomPicture(Gtk.Widget):
    """Texture view with fit-to-window, zoom and pan.

    The scale is the on-screen size over the texture's pixel size, so 1.0
    is "100%". While *fitted* the scale follows the widget size (and never
    upscales); any explicit zoom leaves the fit mode, and zooming back out
    to the fit scale returns to it.
    """

    __gtype_name__ = 'BabbleZoomPicture'
    __gsignals__ = {
        'zoom-changed': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, **kwargs):
        super().__init__(hexpand=True, vexpand=True, focusable=True,
                         overflow=Gtk.Overflow.HIDDEN,
                         accessible_role=Gtk.AccessibleRole.IMG, **kwargs)
        self.add_css_class('media-viewer-picture')
        self.texture = None
        self.fitted = True
        self._scale = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._drag_origin = (0.0, 0.0)
        self._pinch_origin = 1.0

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        pages.connect_weak(drag, 'drag-begin', self._on_drag_begin)
        pages.connect_weak(drag, 'drag-update', self._on_drag_update)
        self.add_controller(drag)

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        pages.connect_weak(click, 'pressed', self._on_pressed)
        self.add_controller(click)

        pinch = Gtk.GestureZoom()
        pages.connect_weak(pinch, 'begin', self._on_pinch_begin)
        pages.connect_weak(pinch, 'scale-changed', self._on_pinch)
        self.add_controller(pinch)

        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        pages.connect_weak(scroll, 'scroll', self._on_scroll)
        self.add_controller(scroll)

    # Geometry

    def set_texture(self, texture):
        self.texture = texture
        self.fitted = True
        self._pan_x = self._pan_y = 0.0
        self.queue_draw()
        self._changed()

    def fit_scale(self):
        if self.texture is None:
            return 1.0
        width, height = self.get_width(), self.get_height()
        if width <= 0 or height <= 0:
            return 1.0
        return min(width / self.texture.get_width(),
                   height / self.texture.get_height(), 1.0)

    @property
    def scale(self):
        return self.fit_scale() if self.fitted else self._scale

    def can_zoom_in(self):
        return self.texture is not None and self.scale < self._max_scale()

    def can_zoom_out(self):
        return self.texture is not None and not self.fitted

    def _max_scale(self):
        return max(MAX_SCALE, self.fit_scale())

    def _scaled_size(self, scale):
        return (self.texture.get_width() * scale,
                self.texture.get_height() * scale)

    def _clamp_pan(self):
        if self.texture is None:
            return
        size_x, size_y = self._scaled_size(self.scale)
        limit_x = max(0.0, (size_x - self.get_width()) / 2)
        limit_y = max(0.0, (size_y - self.get_height()) / 2)
        self._pan_x = min(max(self._pan_x, -limit_x), limit_x)
        self._pan_y = min(max(self._pan_y, -limit_y), limit_y)

    def _changed(self):
        self.set_cursor_from_name(
            None if self.fitted else
            ('grabbing' if self._dragging else 'grab'))
        self.emit('zoom-changed')

    _dragging = False

    def zoom_to(self, scale, anchor=None):
        """Zoom to *scale* keeping the image point under *anchor* (widget
        coordinates, default the centre) in place."""
        if self.texture is None:
            return
        width, height = self.get_width(), self.get_height()
        if anchor is None:
            anchor = (width / 2, height / 2)
        fit = self.fit_scale()
        scale = min(max(scale, fit), self._max_scale())
        old = self.scale
        old_x, old_y = self._scaled_size(old)
        # Image-space point under the anchor before the change.
        origin_x = (width - old_x) / 2 + self._pan_x
        origin_y = (height - old_y) / 2 + self._pan_y
        point_x = (anchor[0] - origin_x) / old
        point_y = (anchor[1] - origin_y) / old
        if scale <= fit * 1.001:
            self.fitted = True
            self._pan_x = self._pan_y = 0.0
        else:
            self.fitted = False
            self._scale = scale
            new_x, new_y = self._scaled_size(scale)
            self._pan_x = anchor[0] - point_x * scale - (width - new_x) / 2
            self._pan_y = anchor[1] - point_y * scale - (height - new_y) / 2
            self._clamp_pan()
        self.queue_draw()
        self._changed()

    def zoom_in(self, anchor=None):
        self.zoom_to(self.scale * ZOOM_STEP, anchor)

    def zoom_out(self, anchor=None):
        self.zoom_to(self.scale / ZOOM_STEP, anchor)

    def zoom_fit(self):
        self.zoom_to(0, None)

    def toggle_actual_size(self, anchor=None):
        """Double-click behaviour: fit <-> 100% (200% for small images
        that already fit at 100%)."""
        if not self.fitted:
            self.zoom_fit()
        elif self.fit_scale() < 0.999:
            self.zoom_to(1.0, anchor)
        else:
            self.zoom_to(2.0, anchor)

    def pan_by(self, dx, dy):
        if self.fitted:
            return False
        self._pan_x += dx
        self._pan_y += dy
        self._clamp_pan()
        self.queue_draw()
        return True

    # Widget vfuncs

    def do_measure(self, orientation, for_size):
        return (1, 400, -1, -1)

    def do_size_allocate(self, width, height, _baseline):
        self._clamp_pan()
        self._changed()

    def do_snapshot(self, snapshot):
        if self.texture is None:
            return
        width, height = self.get_width(), self.get_height()
        scale = self.scale
        size_x, size_y = self._scaled_size(scale)
        rect = Graphene.Rect().init(
            (width - size_x) / 2 + self._pan_x,
            (height - size_y) / 2 + self._pan_y, size_x, size_y)
        scaling = (Gsk.ScalingFilter.TRILINEAR if scale < 1.0
                   else Gsk.ScalingFilter.LINEAR)
        snapshot.append_scaled_texture(self.texture, scaling, rect)

    # Gestures

    def _on_drag_begin(self, gesture, x, y):
        self._drag_origin = (self._pan_x, self._pan_y)
        self._dragging = True
        self._changed()
        self.grab_focus()

    def _on_drag_update(self, gesture, dx, dy):
        if self.fitted:
            return
        self._pan_x = self._drag_origin[0] + dx
        self._pan_y = self._drag_origin[1] + dy
        self._clamp_pan()
        self.queue_draw()

    def _on_pressed(self, gesture, n_press, x, y):
        self._dragging = False
        self._changed()
        if n_press == 2:
            self.toggle_actual_size((x, y))

    def _on_pinch_begin(self, gesture, _sequence):
        self._pinch_origin = self.scale

    def _on_pinch(self, gesture, factor):
        ok, x, y = gesture.get_bounding_box_center()
        self.zoom_to(self._pinch_origin * factor, (x, y) if ok else None)

    def _on_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if state & Gdk.ModifierType.CONTROL_MASK:
            event = controller.get_current_event()
            position = event.get_position() if event is not None else None
            anchor = None
            if position is not None and position[0]:
                ok, point = self.get_native().compute_point(
                    self, Graphene.Point().init(position[1], position[2]))
                if ok:
                    anchor = (point.x, point.y)
            self.zoom_to(self.scale * (1.1 ** -dy), anchor)
            return True
        return self.pan_by(-dx * 40, -dy * 40)
