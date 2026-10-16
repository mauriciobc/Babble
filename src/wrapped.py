# SPDX-License-Identifier: GPL-3.0-or-later

"""Wrapstodon, the yearly summary of your posting.

``present_wrapped(parent, client, year=None)`` opens a dialog with the
annual report of *year* (the latest one when omitted): archetype, post
count, new followers and follows, top hashtag and app, the accounts you
interacted with most, a month by month graph and your most boosted,
favorited and replied-to posts. Servers without a report (or accounts
that are not eligible) get a plain empty state.

Tuba: Dialogs/AnnualReport.vala, API/AnnualReports.vala. Phanpy:
pages/annual-report.jsx, year-in-posts.jsx. Mastodon:
docs.joinmastodon.org/methods/annual_reports/.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango  # noqa: E402

from . import charts  # noqa: E402
from . import pages  # noqa: E402
from .mastodon import (summarize_error,  # noqa: E402
                       summarize_error_markup)
from .statustext import compact_count  # noqa: E402

# (name, description), the way Mastodon's own report words them.
ARCHETYPES = {
    'booster': (
        _('The Archer'),
        _('You hunt down posts to boost, amplifying other creators with '
          'perfect aim.')),
    'lurker': (
        _('The Lurker'),
        _('We know you were out there, somewhere, enjoying Mastodon in '
          'your own quiet way.')),
    'oracle': (
        _('The Oracle'),
        _('You post new posts more than replies, keeping Mastodon fresh '
          'and future-facing.')),
    'pollster': (
        _('The Wizard'),
        _('You create more polls than other post types, cultivating '
          'curiosity on Mastodon.')),
    'replier': (
        _('The Butterfly'),
        _("You frequently reply to other people's posts, pollinating "
          'Mastodon with new discussions.')),
}

STYLES = (
    ('window', _('Default')),
    ('accent', _('Accent')),
    ('pride', _('Pride')),
    ('trans', _('Trans')),
)


def shorten(value):
    """A count for a Wrapped stat card: 1234 -> '1.2K'.

    Delegates to statustext.compact_count, which is the app's one count
    formatter: this used to be a second one with a hardcoded 'k', so a
    translated reader saw a suffix no other counter used."""
    return compact_count(value)


def _send(client, method, path, on_ok=None, on_error=None, **kwargs):
    def finished(_client, result, _data=None):
        try:
            data = client.request_finish(result)
        except GLib.Error as error:
            if on_error is not None:
                on_error(error)
            return
        if on_ok is not None:
            on_ok(data)

    return client.request_async(method, path, callback=finished, **kwargs)


def default_year():
    """The year a fresh report is about: this one in December, else the
    last one."""
    now = GLib.DateTime.new_now_local()
    return now.get_year() if now.get_month() == 12 else now.get_year() - 1


def _draw_chart(area, cr, width, height):
    # Module-level draw func: a bound one would pin the chart through
    # itself.
    area._draw(area, cr, width, height)


class MonthlyChart(Gtk.DrawingArea):
    """Twelve bars, one per month, with the month under each."""

    __gtype_name__ = 'BabbleWrappedChart'

    def __init__(self, series, accent=True, label=None, **kwargs):
        super().__init__(content_height=150, hexpand=True, **kwargs)
        self.series = series  # {month (1-12): value}
        self.accent = accent
        self.set_draw_func(_draw_chart)
        self.month_names = [
            GLib.DateTime.new_local(2000, month, 1, 0, 0, 0).format('%b')
            for month in range(1, 13)]
        if label:
            self.update_property([Gtk.AccessibleProperty.LABEL], [label])

    def _color(self):
        if self.accent:
            return Adw.StyleManager.get_default().get_accent_color_rgba()
        return self.get_color()

    def _draw(self, area, cr, width, height):
        text = area.get_color()
        bar = self._color()
        values = [self.series.get(month, 0) for month in range(1, 13)]
        peak = max(values) or 1
        label_h = 18
        top = 18
        plot_h = height - label_h - top
        slot = width / 12
        bar_w = slot * 0.6
        cr.set_font_size(11)
        for index, value in enumerate(values):
            x = index * slot + (slot - bar_w) / 2
            bar_h = max(plot_h * value / peak, 2 if value else 0)
            cr.set_source_rgba(bar.red, bar.green, bar.blue, charts.alpha(0.9))
            cr.rectangle(x, top + plot_h - bar_h, bar_w, bar_h)
            cr.fill()
            cr.set_source_rgba(text.red, text.green, text.blue,
                               charts.alpha(charts.DIMMED))
            name = self.month_names[index]
            extents = cr.text_extents(name)
            cr.move_to(index * slot + (slot - extents.width) / 2,
                       height - 4)
            cr.show_text(name)
            if value:
                shown = shorten(value)
                extents = cr.text_extents(shown)
                cr.move_to(index * slot + (slot - extents.width) / 2,
                           top + plot_h - bar_h - 4)
                cr.show_text(shown)


def stat_card(title, value, subtitle=None):
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                  valign=Gtk.Align.CENTER, margin_top=12, margin_bottom=12,
                  margin_start=12, margin_end=12)
    box.add_css_class('wrapped-stat')
    caption = Gtk.Label(label=title, wrap=True, justify=Gtk.Justification.CENTER)
    caption.add_css_class('caption-heading')
    caption.add_css_class('dimmed')
    big = Gtk.Label(label=value, wrap=True, justify=Gtk.Justification.CENTER,
                    wrap_mode=Pango.WrapMode.WORD_CHAR)
    big.add_css_class('title-2')
    box.append(caption)
    box.append(big)
    if subtitle:
        small = Gtk.Label(label=subtitle, wrap=True,
                          justify=Gtk.Justification.CENTER)
        small.add_css_class('caption')
        small.add_css_class('dimmed')
        box.append(small)
    box.add_css_class('card')
    return box


def section_title(text):
    label = Gtk.Label(label=text, xalign=0.0, wrap=True)
    label.add_css_class('title-3')
    return label


class WrappedDialog(Adw.Dialog):
    """The dialog; renders ``GET /api/v1/annual_reports``."""

    def __init__(self, parent, client, year=None, **kwargs):
        super().__init__(title=_('Wrapped'), content_width=560,
                         content_height=760, **kwargs)
        self.parent = parent
        self.client = client
        self.year = year
        self.data = None
        self.report = None
        self.style = 'window'
        self._generation = 0

        self.stack = Gtk.Stack()
        self.toasts = Adw.ToastOverlay(child=self.stack)
        self.toolbar = Adw.ToolbarView(content=self.toasts)
        self.header = Adw.HeaderBar()
        self.toolbar.add_top_bar(self.header)
        self.set_child(self.toolbar)

        self.save_button = pages.icon_button(
            'document-save-symbolic', _('Save as Image…'))
        pages.connect_weak(self.save_button, 'clicked',
                           self._on_save_clicked)
        self.save_button.set_visible(False)
        self.header.pack_end(self.save_button)
        self.style_button = Gtk.MenuButton(
            label=_('Style'), direction=Gtk.ArrowType.NONE, visible=False)
        self.style_button.set_menu_model(self._style_menu())
        self.header.pack_start(self.style_button)
        self.year_dropdown = Gtk.DropDown(visible=False)
        # A tooltip does not set the accessible name, and this control
        # has no text of its own, so it needs both.
        self.year_dropdown.set_tooltip_text(_('Year'))
        self.year_dropdown.update_property([Gtk.AccessibleProperty.LABEL],
                                           [_('Year')])
        pages.connect_weak(self.year_dropdown, 'notify::selected',
                           self._on_year)
        self.header.pack_start(self.year_dropdown)

        actions = Gio.SimpleActionGroup()
        action = Gio.SimpleAction.new_stateful(
            'style', GLib.VariantType.new('s'),
            GLib.Variant.new_string('window'))
        pages.connect_weak(action, 'activate', self._on_style)
        actions.add_action(action)
        self.insert_action_group('wrapped', actions)

        self.stack.add_named(pages.make_loading_page(_('Loading')),
                             'loading')
        self.error_page = pages.make_error_page(
            _('Could Not Load'), self._on_retry)
        self.stack.add_named(self.error_page, 'error')
        self.status_page = Adw.StatusPage()
        self.stack.add_named(self.status_page, 'status')
        self._report_widget = None
        self.load()

    def _on_retry(self, _button):
        self.load()

    def _on_save_clicked(self, _button):
        self._on_save()

    # Loading

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, use_markup=False,
                                        timeout=5))

    def _style_menu(self):
        menu = Gio.Menu()
        for key, label in STYLES:
            menu.append(label, "wrapped.style('%s')" % key)
        return menu

    def load(self):
        self._generation += 1
        generation = self._generation
        self.stack.set_visible_child_name('loading')
        self._set_chrome(False)
        path = '/annual_reports'
        if self.year:
            path += '/%d' % self.year

        def ok(data):
            if generation != self._generation:
                return
            if not (data or {}).get('annual_reports'):
                self._check_state(self.year or default_year())
                return
            self.data = data
            self._select(self.year)

        def failed(error):
            if generation != self._generation:
                return
            if error.code in (404, 410):
                self._check_state(self.year or default_year())
                return
            self.error_page.set_description(
                summarize_error_markup(error))
            self.stack.set_visible_child_name('error')

        _send(self.client, 'GET', path, ok, failed)

    def _set_chrome(self, visible):
        self.save_button.set_visible(visible)
        self.style_button.set_visible(visible)

    def _show_status(self, title, description, icon='emoji-symbols-symbolic',
                     button=None, callback=()):
        """*callback* is ``(method_name, *args)`` of this dialog: a
        closure over self would pin the dialog through its button."""
        self.status_page.set_icon_name(icon)
        self.status_page.set_title(title)
        self.status_page.set_description(description)
        if button:
            action = Gtk.Button(label=button, use_underline=True,
                                halign=Gtk.Align.CENTER)
            action.add_css_class('pill')
            action.add_css_class('suggested-action')
            pages.connect_weak(action, 'clicked', self._on_status_clicked,
                               *callback)
            self.status_page.set_child(action)
        else:
            self.status_page.set_child(None)
        self.year_dropdown.set_visible(False)
        self._set_chrome(False)
        self.stack.set_visible_child_name('status')

    def _on_status_clicked(self, _button, method, *args):
        getattr(self, method)(*args)

    def _empty(self, year):
        self._show_status(
            _('No Wrapped for %d') % year,
            _('Your server has not published a report for you. Reports '
              'appear around the end of the year for accounts that '
              'posted.'))

    def _check_state(self, year):
        """Servers can generate a report on request: look at the
        ``/state`` endpoint (Mastodon 4.5+) before giving up."""
        generation = self._generation

        def ok(data):
            if generation != self._generation:
                return
            state = (data or {}).get('state')
            if state == 'eligible':
                self._show_status(
                    _('Your %d Wrapped Is Ready to Make') % year,
                    _('Your server can put it together now. It takes a '
                      'little while.'), button=_('_Generate'),
                    callback=('_generate', year))
            elif state == 'generating':
                self._show_status(
                    _('Putting Your %d Wrapped Together') % year,
                    _('Check again in a few minutes.'),
                    icon='content-loading-symbolic',
                    button=_('_Check Again'), callback=('load',))
            elif state == 'available':
                self.year = year
                self.load()
            else:
                self._empty(year)

        _send(self.client, 'GET', '/annual_reports/%d/state' % year, ok,
              lambda _e: generation == self._generation and self._empty(year))

    def _generate(self, year):
        self.stack.set_visible_child_name('loading')

        def ok(_data):
            self._show_status(
                _('Putting Your %d Wrapped Together') % year,
                _('Check again in a few minutes.'),
                icon='content-loading-symbolic', button=_('_Check Again'),
                callback=('load',))

        def failed(error):
            self._check_state(year)
            self.toast(summarize_error(error))

        _send(self.client, 'POST', '/annual_reports/%d/generate' % year, ok,
              failed)

    # Rendering

    def _select(self, year):
        reports = sorted(self.data['annual_reports'],
                         key=lambda r: r.get('year', 0), reverse=True)
        self._reports = reports
        years = [r.get('year') for r in reports]
        if len(years) > 1:
            self.year_dropdown.set_model(
                Gtk.StringList.new([str(y) for y in years]))
            self.year_dropdown.set_visible(True)
        else:
            self.year_dropdown.set_visible(False)
        index = years.index(year) if year in years else 0
        self.year_dropdown.set_selected(index)
        self._render(reports[index])

    def _on_year(self, dropdown, _pspec):
        reports = getattr(self, '_reports', None)
        index = dropdown.get_selected()
        if reports and 0 <= index < len(reports) \
                and reports[index] is not self.report:
            self._render(reports[index])

    def _render(self, report):
        self.report = report
        if self._report_widget is not None:
            self.stack.remove(self._report_widget)
        scroller, self.card = self._build(report)
        self._report_widget = scroller
        self.stack.add_named(scroller, 'report')
        self.stack.set_visible_child_name('report')
        self._set_chrome(True)
        self._apply_style()
        self.set_title(_('%d Wrapped') % report['year'])

    def _account(self, account_id):
        for account in self.data.get('accounts') or []:
            if account.get('id') == account_id:
                return account
        return None

    def _me(self):
        window = pages.window_of(self.parent)
        return getattr(window, 'account', None) or {}

    def _build(self, report):
        from .mediacache import AvatarCache
        data = report.get('data') or {}
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                       margin_top=18, margin_bottom=18, margin_start=12,
                       margin_end=12)
        card.add_css_class('wrapped-card')

        me = self._me()
        avatar = Adw.Avatar(size=96, halign=Gtk.Align.CENTER,
                            text=pages.display_name(me) if me else '?',
                            show_initials=True)
        if me:
            AvatarCache.load(avatar, me)
        card.append(avatar)
        title = Gtk.Label(label=_('%d Wrapped') % report['year'],
                          wrap=True, halign=Gtk.Align.CENTER)
        title.add_css_class('title-1')
        card.append(title)

        archetype = data.get('archetype')
        if archetype in ARCHETYPES:
            name, description = ARCHETYPES[archetype]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                          halign=Gtk.Align.CENTER)
            kicker = Gtk.Label(label=_('Your archetype'))
            kicker.add_css_class('caption-heading')
            kicker.add_css_class('dimmed')
            box.append(kicker)
            big = Gtk.Label(label=name)
            big.add_css_class('title-2')
            box.append(big)
            text = Gtk.Label(label=description, wrap=True,
                             justify=Gtk.Justification.CENTER,
                             max_width_chars=40)
            text.add_css_class('dimmed')
            box.append(text)
            card.append(box)

        series = data.get('time_series') or []
        posts = sum(m.get('statuses') or 0 for m in series)
        gained = sum(m.get('followers') or 0 for m in series)
        followed = sum(m.get('following') or 0 for m in series)

        stats = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE,
                            column_spacing=8, row_spacing=8,
                            min_children_per_line=1, max_children_per_line=2,
                            homogeneous=True)
        if series:
            stats.append(stat_card(_('New Posts'), shorten(posts)))
        hashtags = sorted(data.get('top_hashtags') or [],
                          key=lambda h: h.get('count', 0), reverse=True)
        if hashtags:
            stats.append(stat_card(_('Most Used Hashtag'),
                                   '#' + hashtags[0]['name'],
                                   ngettext('%d post', '%d posts',
                                            hashtags[0].get('count', 0))
                                   % hashtags[0].get('count', 0)))
        apps = sorted(data.get('most_used_apps') or [],
                      key=lambda a: a.get('count', 0), reverse=True)
        if apps:
            stats.append(stat_card(_('Most Used App'), apps[0]['name']))
        if gained:
            stats.append(stat_card(_('New Followers'), shorten(gained)))
        if followed:
            stats.append(stat_card(_('New Follows'), shorten(followed)))
        if stats.get_first_child() is not None:
            card.append(stats)

        interacted = sorted(
            data.get('commonly_interacted_with_accounts') or [],
            key=lambda a: a.get('count', 0), reverse=True)[:3]
        people = [self._account(item.get('account_id'))
                  for item in interacted]
        people = [account for account in people if account]
        if people:
            card.append(self._people_card(
                _('Most Interactions'), people))

        if series and (posts or gained):
            card.append(section_title(_('Your Year')))
            if posts:
                card.append(self._chart_block(
                    _('Posts per Month'),
                    {m['month']: m.get('statuses') or 0 for m in series},
                    True))
            if gained:
                card.append(self._chart_block(
                    _('New Followers per Month'),
                    {m['month']: m.get('followers') or 0 for m in series},
                    False))

        if hashtags:
            card.append(self._count_group(
                _('Top Hashtags'),
                [('#' + h['name'], h.get('count', 0)) for h in hashtags[:5]]))
        if apps:
            card.append(self._count_group(
                _('Top Apps'),
                [(a['name'], a.get('count', 0)) for a in apps[:5]]))

        top = data.get('top_statuses') or {}
        for key, heading in (('by_favourites', _('Most Favorited Post')),
                             ('by_reblogs', _('Most Boosted Post')),
                             ('by_replies', _('Most Replied-to Post'))):
            status_id = top.get(key)
            if status_id:
                card.append(self._top_post(heading, status_id))

        clamp = Adw.Clamp(maximum_size=pages.COLUMN_WIDTH, child=card)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True, child=clamp)
        return scroller, card

    def _chart_block(self, heading, series, accent):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        label = Gtk.Label(label=heading, xalign=0.0)
        label.add_css_class('heading')
        box.append(label)
        chart = MonthlyChart(series, accent=accent, label=heading)
        chart.add_css_class('wrapped-chart')
        box.append(chart)
        return box

    def _people_card(self, heading, accounts):
        from .mediacache import AvatarCache
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                      margin_top=12, margin_bottom=12)
        box.add_css_class('card')
        caption = Gtk.Label(label=heading)
        caption.add_css_class('caption-heading')
        caption.add_css_class('dimmed')
        box.append(caption)
        row = Gtk.Box(spacing=18, halign=Gtk.Align.CENTER)
        for account in accounts:
            name = pages.display_name(account)
            cell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                           halign=Gtk.Align.CENTER, width_request=90)
            avatar = Adw.Avatar(size=56, text=name, show_initials=True,
                                halign=Gtk.Align.CENTER)
            AvatarCache.load(avatar, account)
            cell.append(avatar)
            label = Gtk.Label(label=name, ellipsize=Pango.EllipsizeMode.END,
                              max_width_chars=12)
            label.add_css_class('caption')
            cell.append(label)
            row.append(cell)
        box.append(row)
        return box

    def _count_group(self, heading, entries):
        group = Adw.PreferencesGroup(title=heading)
        for name, count in entries:
            row = Adw.ActionRow(title=name, use_markup=False)
            badge = Gtk.Label(label=shorten(count), valign=Gtk.Align.CENTER)
            badge.add_css_class('numeric')
            badge.add_css_class('dimmed')
            row.add_suffix(badge)
            group.add(row)
        return group

    def _top_post(self, heading, status_id):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.append(section_title(heading))
        holder = Gtk.Box()
        box.append(holder)
        status = next((s for s in self.data.get('statuses') or []
                       if s.get('id') == status_id), None)
        if status is not None:
            self._put_status(holder, status)
        else:
            # The report only embeds some posts; fetch the rest.
            _send(self.client, 'GET', '/statuses/%s' % status_id,
                  lambda s: self._put_status(holder, s),
                  lambda _e: box.set_visible(False))
        return box

    def _put_status(self, holder, status):
        from .timelinedata import StatusItem
        from .statusrow import StatusRow
        row = StatusRow()
        row.bind(StatusItem(status), self.client)
        row.set_hexpand(True)
        for name in ('actions', '_overflow'):
            widget = getattr(row, name, None)
            if widget is not None:
                widget.set_visible(False)
        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        pages.connect_weak(click, 'released', self._on_status_released,
                           status)
        row.add_controller(click)
        holder.append(row)

    def _on_status_released(self, _click, _n_press, _x, _y, status):
        self._open_status(status)

    def _open_status(self, status):
        parent = self.parent
        self.close()
        pages.open_status(parent, self.client, status)

    # Style and export

    def _on_style(self, action, value):
        self.style = value.get_string()
        action.set_state(value)
        self._apply_style()

    def _apply_style(self):
        card = getattr(self, 'card', None)
        if card is None:
            return
        for key, _label in STYLES:
            card.remove_css_class('wrapped-style-' + key)
        card.add_css_class('wrapped-style-' + self.style)

    def _on_save(self):
        texture = self._screenshot()
        if texture is None:
            self.toast(_('Could not render the image'))
            return
        year = self.report['year']
        dialog = Gtk.FileDialog(title=_('Save Wrapped'),
                                initial_name='%d-wrapped.png' % year)

        def chosen(dlg, result):
            try:
                file = dlg.save_finish(result)
            except GLib.Error:
                return
            try:
                file.replace_contents(
                    texture.save_to_png_bytes().get_data(), None, False,
                    Gio.FileCreateFlags.PRIVATE, None)
            except GLib.Error as error:
                self.toast(error.message)
                return
            self.toast(_('Saved %d Wrapped') % year)

        dialog.save(pages.window_of(self.parent), None, chosen)

    def _screenshot(self):
        card = self.card
        width, height = card.get_width(), card.get_height()
        native = card.get_native()
        if not (width and height and native):
            return None
        snapshot = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(card).snapshot(snapshot, width, height)
        node = snapshot.to_node()
        if node is None:
            return None
        rect = Graphene.Rect().init(0, 0, width, height)
        return native.get_renderer().render_texture(node, rect)


def present_wrapped(parent_widget, client, year=None):
    """Open the annual report dialog over *parent_widget*'s window."""
    dialog = WrappedDialog(parent_widget, client, year)
    dialog.present(parent_widget)
    return dialog
