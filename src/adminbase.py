# SPDX-License-Identifier: GPL-3.0-or-later
"""Admin shared scaffolding: permissions, error text and list chrome.

Everything the moderation tools build on: the role permission bits and
the checks over them, the error/date/account formatting the pages share,
the `CtxRef` request context, the sparkline, the state page, the row
helpers and the add bar. It imports nothing from ``admin``; the pages do
the importing.
"""

from urllib.parse import urlsplit  # noqa: E402

from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from .i18n import gettext as _  # noqa: E402

from . import charts  # noqa: E402
from . import pages, uihelpers  # noqa: E402
from .mastodon import summarize_error  # noqa: E402
from .statustext import format_time  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402



# Role permission bits (Mastodon UserRole::FLAGS)
PERM_ADMINISTRATOR = 1

PERM_REPORTS = 16

PERM_FEDERATION = 32

PERM_BLOCKS = 128

PERM_USERS = 1024

def _admin_line(error):
    """One readable line for a GLib.Error from an admin call."""
    code = getattr(error, 'code', 0)
    message = summarize_error(error)
    if code in (401, 403):
        return _('%s. This login is not authorized for moderation on this '
                 'server.') % message.rstrip('.')
    return message

def error_text(error):
    """*error* as one plain-text line, for a ``use_markup=False`` toast.

    Goes through ``_admin_line`` rather than summarize_error directly:
    a 401/403 from a moderation call means the login lacks the admin
    scopes, and that is worth saying in words rather than leaving the
    reader with a bare status code.
    """
    return _admin_line(error)

def error_text_markup(error):
    """*error* as one line escaped for a markup-rendering widget.

    A status page parses its description as markup, so a bare ``&`` in
    a server body fails the parse and blanks the label.
    """
    return GLib.markup_escape_text(_admin_line(error))

def format_date(iso, with_time=True):
    """The locale's short date of *iso*, then the time in the desktop's
    clock format unless *with_time* is false; '' when unparsable."""
    value = GLib.DateTime.new_from_iso8601(iso, None) if iso else None
    if value is None:
        return ''
    local = value.to_local()
    date = local.format('%x')
    if not with_time:
        return date
    return '%s %s' % (date, format_time(local, with_date=False))

def account_handle(admin_account, client):
    """'@name@domain' for an Admin::Account, local accounts get the
    instance's host."""
    username = admin_account.get('username') \
        or admin_account.get('account', {}).get('username', '')
    domain = admin_account.get('domain')
    if not domain:
        domain = urlsplit(getattr(client, 'instance', '') or '').netloc
    return '@%s@%s' % (username, domain) if domain else '@' + username

def account_name(admin_account):
    account = admin_account.get('account') or {}
    return account.get('display_name') or admin_account.get('username') \
        or account.get('acct', '')

def send(client, start, finish, on_ok=None, on_error=None):
    """One request; *on_ok(data)* or *on_error(GLib.Error)*.

    *start(callback)* queues it, so the path is never spelled here:
    ``lambda cb: admin_api.measures_async(client, body, callback=cb)``;
    *finish* is that module's ``<name>_finish``
    (``admin_api.measures_finish``)."""

    def finished(_client, result, _data=None):
        try:
            data = finish(client, result)
        except GLib.Error as error:
            if on_error is not None:
                on_error(error)
            return
        if on_ok is not None:
            on_ok(data)

    return start(finished)

def bool_str(value):
    return 'true' if value else 'false'

class CtxRef:
    """Mixin holding ``ctx`` (the AdminDialog) weakly: the pages live
    inside it, so a strong reference would cycle through C."""

    @property
    def ctx(self):
        return self._ctx()

    @ctx.setter
    def ctx(self, ctx):
        self._ctx = ctx.weak_ref()

def permission_allows(permissions, mask):
    """*permissions* is the role bitmask, None when unknown (the server
    decides then)."""
    if permissions is None:
        return True
    return bool(permissions & PERM_ADMINISTRATOR or permissions & mask)

def role_permissions(account):
    role = (account or {}).get('role') or {}
    value = role.get('permissions')
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def needs_admin_scopes(error):
    """Whether *error* is the token missing the admin scopes.

    Only the scope refusal is something this app can fix, by asking the
    user to authorize them. A 403 whose body does not name the scopes is
    the role refusing the request, and offering another authorization
    for it would send the user to a consent screen that changes nothing.

    Mastodon answers the scope refusal with ``This action is outside the
    authorized scopes``; a role refusal is a bare ``Forbidden``. Keying
    on the status alone would mistake the second for the first.
    """
    if getattr(error, 'code', 0) not in (401, 403):
        return False
    message = summarize_error(getattr(error, 'message', None) or '')
    return 'scope' in message.lower()

class Sparkline(Gtk.DrawingArea):
    """Tiny line chart of a number series, drawn in the text colour."""

    __gtype_name__ = 'BabbleAdminSparkline'

    def __init__(self, values, **kwargs):
        super().__init__(content_width=96, content_height=28,
                         valign=Gtk.Align.CENTER, **kwargs)
        self.values = list(values)
        # A plain function: a bound method would cycle through C.
        self.set_draw_func(_draw_sparkline)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [_('Trend over the selected period')])

    def _draw(self, area, cr, width, height):
        if len(self.values) < 2:
            return
        low, high = min(self.values), max(self.values)
        span = (high - low) or 1
        color = area.get_color()
        cr.set_source_rgba(color.red, color.green, color.blue,
                          charts.alpha(0.8))
        cr.set_line_width(1.5)
        step = width / (len(self.values) - 1)
        for index, value in enumerate(self.values):
            x = index * step
            y = height - 3 - (value - low) / span * (height - 6)
            if index == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)
        cr.stroke()

def _draw_sparkline(area, cr, width, height):
    area._draw(area, cr, width, height)

class StatePage(CtxRef, Adw.Bin):
    """Loading / error / content states for pages that fetch by hand."""

    def __init__(self, ctx, **kwargs):
        super().__init__(**kwargs)
        self.ctx = ctx
        self._content = None
        self.stack = pages.StateStack(
            _('Loading'), _('Could Not Load'), self._on_retry,
            markup=error_text_markup)
        self.error_page = self.stack.error_page
        self.set_child(self.stack)

    def _on_retry(self, _button):
        self.refresh()

    def show_loading(self):
        self.stack.show_loading()

    def show_error(self, error):
        self.stack.show_error(error)

    def show_content(self, widget):
        if self._content is not None:
            self.stack.remove(self._content)
        self._content = widget
        self.stack.add_named(widget, 'content')
        self.stack.show('content')

    def refresh(self):
        raise NotImplementedError

def count_badge_label(text, css=None):
    label = Gtk.Label(label=text, xalign=1.0, wrap=True, hexpand=True,
                      valign=Gtk.Align.CENTER,
                      wrap_mode=Pango.WrapMode.WORD_CHAR)
    label.add_css_class('dimmed')
    label.add_css_class('caption')
    if css:
        label.add_css_class(css)
    return label

def delete_button(tooltip, method, *extra):
    """Trash button calling the bound *method* ``(button, *extra)``,
    held weakly."""
    button = uihelpers.icon_button('user-trash-symbolic', tooltip)
    connect_weak(button, 'clicked', method, *extra)
    button.add_css_class('error')
    return button

def avatar_for(account, size=48):
    from .mediacache import AvatarCache
    name = account.get('display_name') or account.get('username') or '?'
    avatar = Adw.Avatar(size=size, text=name, show_initials=True)
    AvatarCache.load(avatar, account)
    return avatar

def status_label(item):
    """Moderation state of an Admin::Account, as a short word."""
    if item.get('suspended'):
        return _('Suspended')
    if item.get('silenced'):
        return _('Limited')
    if item.get('disabled'):
        return _('Disabled')
    if item.get('approved') is False:
        return _('Waiting Approval')
    return _('No Limits')

class AccountSummary(Adw.ActionRow):
    """Avatar, name and handle of an Admin::Account (not clickable)."""

    def __init__(self, client, admin_account, **kwargs):
        super().__init__(activatable=False, use_markup=False,
                         subtitle_selectable=True, **kwargs)
        account = admin_account.get('account') or {}
        self.set_title(account_name(admin_account))
        self.set_subtitle(account_handle(admin_account, client))
        self.add_prefix(avatar_for(account, 48))

def info_row(title, value, selectable=True):
    row = Adw.ActionRow(title=title, subtitle=value or _('None'),
                        use_markup=False, subtitle_selectable=selectable)
    row.add_css_class('property')
    return row

class AddBar(Gtk.Box):
    """Entry plus button on top of a list (allow/block a domain)."""

    def __init__(self, placeholder, button_label, on_submit):
        super().__init__(spacing=6, margin_top=6, margin_bottom=6,
                         margin_start=12, margin_end=12)
        self.on_submit = on_submit
        self.entry = Gtk.Entry(placeholder_text=placeholder, hexpand=True,
                               input_purpose=Gtk.InputPurpose.URL)
        self.entry.update_property([Gtk.AccessibleProperty.LABEL],
                                   [placeholder])
        self.button = Gtk.Button(label=button_label, sensitive=False)
        self.button.add_css_class('suggested-action')
        self.append(self.entry)
        self.append(self.button)
        connect_weak(self.entry, 'notify::text', self._on_changed)
        connect_weak(self.entry, 'activate', self._on_submit)
        connect_weak(self.button, 'clicked', self._on_submit)
        self.set_halign(Gtk.Align.CENTER)
        self.set_size_request(420, -1)

    def _on_changed(self, *_args):
        self.button.set_sensitive(bool(self.entry.get_text().strip()))

    def _on_submit(self, *_args):
        value = self.entry.get_text().strip()
        if value:
            self.on_submit(value)

    def clear(self):
        self.entry.set_text('')
