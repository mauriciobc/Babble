# SPDX-License-Identifier: GPL-3.0-or-later

"""Collections (Mastodon 4.6+, API version 10): curated lists of
accounts to recommend.

The page has two tabs, the collections you made and the collections
that feature you. A collection opens as its own page; the create/edit
dialog manages name, description, language, topic, visibility,
sensitivity and members.

Tuba: Views/Collections.vala, Dialogs/Collection.vala,
Dialogs/Collections.vala, Widgets/Collection.vala, API/Collection.vala.
Phanpy: pages/account-collections.jsx, collection.jsx,
components/collection-*.jsx. Mastodon:
docs.joinmastodon.org/methods/collections/.

This module is named after the feature; inside the package it is only
ever imported relatively, so it does not shadow the standard library's
``collections``.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, Gio, GLib, Gtk, Pango  # noqa: E402

from . import pages  # noqa: E402
from .pages import weak_method  # noqa: E402
from .mastodon import (summarize_error,  # noqa: E402
                       summarize_error_markup)

MAX_MEMBERS = 25
DEFAULT_LIMIT = 10
SEARCH_DELAY_MS = 400


def _items(data):
    """The collection array of a list response (the documentation's
    example spells the key ``collections:``)."""
    if isinstance(data, dict):
        return data.get('collections') or data.get('collections:') or []
    return data or []


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


def _current_account(widget=None):
    """The signed-in account dict of the window around *widget* (or the
    active window)."""
    window = pages.window_of(widget) if widget is not None else None
    if window is None:
        app = Gio.Application.get_default()
        window = app.get_active_window() if app is not None else None
    return getattr(window, 'account', None) or {}


def collection_limit(account):
    role = (account or {}).get('role') or {}
    try:
        return int(role.get('collection_limit') or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


# Account lookup shared by every row: collections only carry account ids.

_ACCOUNTS = {}


def fetch_accounts(client, ids, callback):
    """``callback({id: account})`` once the accounts for *ids* are known;
    one batched ``GET /accounts?id[]=…`` for the ones not cached."""
    ids = [i for i in dict.fromkeys(ids) if i]
    host = getattr(client, 'instance', '')

    def done():
        callback({i: _ACCOUNTS[(host, i)] for i in ids
                  if (host, i) in _ACCOUNTS})

    missing = [i for i in ids if (host, i) not in _ACCOUNTS]
    if not missing:
        done()
        return

    def ok(data):
        for account in data or []:
            _ACCOUNTS[(host, account['id'])] = account
        done()

    _send(client, 'GET', '/accounts', ok, lambda _e: done(),
          params={'id[]': missing})


def remember_account(client, account):
    _ACCOUNTS[(getattr(client, 'instance', ''), account['id'])] = account


def owner_label(account):
    return pages.display_name(account) if account else ''


def format_date(iso, fmt='%x'):
    value = GLib.DateTime.new_from_iso8601(iso, None) if iso else None
    return value.to_local().format(fmt) if value else ''


def language_options():
    from .preferences import LANGUAGES
    return LANGUAGES


def default_language_code(collection=None):
    if collection and collection.get('language'):
        return collection['language']
    code = pages.settings().get_string('default-language')
    return code or 'en'


# Actions shared by rows and the collection page

class CollectionActions:
    """Copy URL, edit, remove myself and delete for one collection."""

    def __init__(self, widget, client, collection, on_changed,
                 on_gone=None):
        self.widget = widget
        self.client = client
        self.collection = collection
        self.on_changed = on_changed
        # Deleting or leaving removes the collection from view.
        self.on_gone = on_gone or on_changed
        self.me = _current_account(widget).get('id')
        self.is_owner = collection.get('account_id') == self.me
        self.my_item = next(
            (item for item in collection.get('items') or []
             if item.get('account_id') == self.me
             and item.get('state') != 'revoked'), None)

    def menu_button(self):
        group = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        entries = [('copy-url', _('Copy URL'))]
        if self.is_owner:
            entries.append(('edit', _('Edit')))
        if self.my_item is not None and not self.is_owner:
            entries.append(('remove-me', _('Remove Myself')))
        if self.is_owner:
            entries.append(('delete', _('Delete')))
        # The widget owns the button and keeps this object as
        # ``widget.actions``: reach it through a GObject weak reference
        # so the handlers do not keep the widget alive.
        widget = self.widget.weak_ref()

        def activate(action, _param):
            owner = widget()
            if owner is not None:
                # 'copy-url' -> copy_url, 'remove-me' -> remove_me, ...
                getattr(owner.actions, action.get_name().replace('-', '_'))()

        for name, label in entries:
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', activate)
            group.add_action(action)
            menu.append(label, 'collection.' + name)
        button = Gtk.MenuButton(icon_name='view-more-symbolic',
                                tooltip_text=_('More Options'),
                                valign=Gtk.Align.CENTER, menu_model=menu)
        button.add_css_class('flat')
        button.update_property([Gtk.AccessibleProperty.LABEL],
                               [_('More Options')])
        button.insert_action_group('collection', group)
        return button

    def copy_url(self):
        url = self.collection.get('url') or self.collection.get('uri')
        if not url:
            return
        self.widget.get_clipboard().set(url)
        pages.show_toast(self.widget, _('Copied URL to clipboard'))

    def edit(self):
        dialog = CollectionEditDialog(self.client, self.collection,
                                      on_saved=self.on_changed)
        dialog.present(self.widget)

    def remove_me(self):
        def go():
            _send(self.client, 'POST',
                  '/collections/%s/items/%s/revoke' % (
                      self.collection['id'], self.my_item['id']),
                  lambda _d: self._gone(_('You were removed from the '
                                          'collection')),
                  lambda e: pages.report_error(
                      self.widget, _('Could not remove you'), e))

        pages.confirm(
            self.widget,
            _('Remove Yourself from “%s”?') % self.collection.get('name', ''),
            _('You will no longer be featured in this collection.'),
            _('_Remove'), go)

    def delete(self):
        def go():
            _send(self.client, 'DELETE',
                  '/collections/%s' % self.collection['id'],
                  lambda _d: self._gone(_('Collection deleted')),
                  lambda e: pages.report_error(
                      self.widget, _('Could not delete the collection'), e))

        pages.confirm(
            self.widget,
            _('Delete “%s”?') % self.collection.get('name', ''),
            _('The accounts in it are not affected.'), _('_Delete'), go)

    def _gone(self, message):
        pages.show_toast(self.widget, message)
        self.on_gone()


# Collection list

class AvatarStack(Gtk.Box):
    """Up to three member avatars (a placeholder for sensitive
    collections), filled once their accounts are fetched."""

    def __init__(self, client, collection, **kwargs):
        super().__init__(spacing=2, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION,
                         **kwargs)
        items = [item for item in collection.get('items') or []
                 if item.get('state') != 'revoked']
        shown = items[:3]
        if collection.get('sensitive'):
            icon = Gtk.Image(icon_name='dialog-warning-symbolic',
                             pixel_size=24)
            icon.add_css_class('dimmed')
            self.append(icon)
            return
        self.avatars = {}
        for item in shown:
            avatar = Adw.Avatar(size=32, text='?', show_initials=False)
            self.avatars[item['account_id']] = avatar
            self.append(avatar)
        if shown:
            fetch_accounts(client, list(self.avatars), self._fill)

    def _fill(self, accounts):
        from .timeline import AvatarCache
        for account_id, avatar in self.avatars.items():
            account = accounts.get(account_id)
            if account is None:
                continue
            avatar.set_text(pages.display_name(account))
            avatar.set_show_initials(True)
            AvatarCache.load(avatar, account)


class CollectionRow(Adw.ActionRow):
    def __init__(self, client, collection, on_changed, **kwargs):
        super().__init__(activatable=True, use_markup=False,
                         subtitle_lines=0, **kwargs)
        self.client = client
        self.collection = collection
        self.set_title(collection.get('name', ''))
        count = collection.get('item_count')
        if count is None:
            count = len(collection.get('items') or [])
        self._count_text = ngettext('%d account', '%d accounts', count) \
            % count
        self.set_subtitle(self._count_text)
        self.add_prefix(AvatarStack(client, collection))
        actions = self.actions = CollectionActions(self, client, collection,
                                                   on_changed)
        self.add_suffix(actions.menu_button())
        if not actions.is_owner:
            fetch_accounts(client, [collection.get('account_id')],
                           self._set_owner)
        elif collection.get('updated_at'):
            self.set_subtitle('%s · %s' % (
                self._count_text,
                _('Updated %s') % format_date(collection['updated_at'])))

    def _set_owner(self, accounts):
        owner = accounts.get(self.collection.get('account_id'))
        if owner is not None:
            self.set_subtitle('%s · %s' % (
                self._count_text, _('By %s') % owner_label(owner)))

    def on_activate(self):
        pages.push_page(
            self, self.collection.get('name', _('Collection')),
            CollectionPage(self.client, self.collection['id'],
                           self.collection),
            tag='collection-%s' % self.collection['id'])


class CollectionsPage(Adw.Bin):
    """My Collections and Featuring Me."""

    __gtype_name__ = 'BabbleCollectionsPage'

    def __init__(self, client, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        account = _current_account(self)
        self.me = account.get('id')
        self.limit = collection_limit(account)
        self._own_count = 0

        self.stack = Adw.ViewStack()
        self.title_widget = Adw.InlineViewSwitcher(
            stack=self.stack, display_mode=Adw.InlineViewSwitcherDisplayMode.LABELS)
        self.new_button = pages.icon_button(
            'list-add-symbolic', _('New Collection'), flat=False)
        pages.connect_weak(self.new_button, 'clicked', self._on_new)
        self.new_button.add_css_class('suggested-action')
        self.header_widgets = [self.new_button]

        self.own = None
        self.featuring = None
        self.set_child(self.stack)
        if self.me:
            self._build()
        else:
            self._load_account()

    def _load_account(self):
        def ok(data):
            self.me = data.get('id')
            self.limit = collection_limit(data)
            self._build()

        def failed(error):
            self.stack.add_titled(
                Adw.StatusPage(icon_name='network-error-symbolic',
                               title=_('Could Not Load'),
                               description=summarize_error_markup(
                                   error.message)),
                'error', _('Collections'))

        _send(self.client, 'GET', '/accounts/verify_credentials', ok, failed)

    def _build(self):
        # The lists and their rows live inside the page: hand them weak
        # callbacks so they do not keep the page alive through C.
        self.own = pages.PagedList(
            self.client, '/accounts/%s/collections' % self.me,
            weak_method(self._make_row), empty_title=_('No Collections'),
            empty_icon='folder-symbolic',
            empty_description=_('Collections you create to recommend '
                                'accounts show up here.'),
            extract=weak_method(self._extract_own))
        self.featuring = pages.PagedList(
            self.client, '/accounts/%s/in_collections' % self.me,
            weak_method(self._make_row), empty_title=_('No Collections'),
            empty_icon='avatar-default-symbolic',
            empty_description=_('Collections other people add you to '
                                'show up here.'),
            extract=_items)
        self.stack.add_titled(self.own, 'own', _('My Collections'))
        self.stack.add_titled(self.featuring, 'featuring',
                              _('Featuring Me'))
        self._update_new_button()

    def _extract_own(self, data):
        items = _items(data)
        if not self.own or not self.own.rows:
            self._own_count = len(items)
        else:
            self._own_count += len(items)
        GLib.idle_add(self._update_new_button)
        return items

    def _update_new_button(self):
        full = self._own_count >= self.limit
        self.new_button.set_sensitive(not full)
        self.new_button.set_tooltip_text(
            ngettext('You have reached the limit of %d collection',
                     'You have reached the limit of %d collections',
                     self.limit) % self.limit
            if full else _('New Collection'))
        return GLib.SOURCE_REMOVE

    def _make_row(self, collection):
        return CollectionRow(self.client, collection, weak_method(self.refresh))

    def _on_new(self, _button):
        dialog = CollectionEditDialog(self.client, None,
                                      on_saved=self.refresh)
        dialog.present(self)

    def refresh(self):
        if self.own is not None:
            self.own.refresh()
        if self.featuring is not None:
            self.featuring.refresh()


# One collection

class CollectionPage(Adw.Bin):
    """GET /collections/:id: header, description and the members."""

    __gtype_name__ = 'BabbleCollectionPage'

    def __init__(self, client, collection_id, collection=None, **kwargs):
        super().__init__(**kwargs)
        self.client = client
        self.collection_id = collection_id
        self.collection = collection
        self.stack = Gtk.Stack()
        self.set_child(self.stack)
        self.stack.add_named(pages.make_loading_page(_('Loading')),
                             'loading')
        self.error_page = pages.make_error_page(
            _('Could Not Load'), self._on_retry)
        self.stack.add_named(self.error_page, 'error')
        self._body = None
        self.refresh()

    def _on_retry(self, _button):
        self.refresh()

    def refresh(self):
        self.stack.set_visible_child_name('loading')

        def ok(data):
            self.collection = data.get('collection') or data
            accounts = data.get('accounts') or []
            for account in accounts:
                remember_account(self.client, account)
            self._show(accounts)

        def failed(error):
            self.error_page.set_description(
                summarize_error_markup(error.message))
            self.stack.set_visible_child_name('error')

        _send(self.client, 'GET', '/collections/%s' % self.collection_id,
              ok, failed)

    def _show(self, accounts):
        collection = self.collection
        by_id = {account['id']: account for account in accounts}
        if self._body is not None:
            self.stack.remove(self._body)
        actions = self._actions = CollectionActions(
            self, self.client, collection, self.refresh, self._pop)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                         margin_top=18, margin_bottom=24, margin_start=12,
                         margin_end=12)

        title = Gtk.Label(label=collection.get('name', ''), wrap=True,
                          wrap_mode=Pango.WrapMode.WORD_CHAR,
                          justify=Gtk.Justification.CENTER,
                          halign=Gtk.Align.CENTER, selectable=True)
        title.add_css_class('title-1')
        column.append(title)

        tag = collection.get('tag')
        if tag and tag.get('name'):
            name = tag['name'].lstrip('#')
            chip = Gtk.Button(label='#' + name, halign=Gtk.Align.CENTER)
            chip.add_css_class('pill')
            pages.connect_weak(chip, 'clicked', self._on_tag_clicked, name)
            column.append(chip)

        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        description = collection.get('description')
        if description:
            label = Gtk.Label(label=description, wrap=True, xalign=0.0,
                              wrap_mode=Pango.WrapMode.WORD_CHAR,
                              selectable=True)
            details.append(label)
        facts = [_('Public') if collection.get('discoverable')
                 else _('Unlisted')]
        if collection.get('language'):
            facts.append(collection['language'].upper())
        if collection.get('updated_at'):
            facts.append(_('Updated %s') % format_date(
                collection['updated_at']))
        meta = Gtk.Label(label=' · '.join(facts), halign=Gtk.Align.CENTER)
        meta.add_css_class('dimmed')
        meta.add_css_class('caption')
        column.append(meta)

        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        copy = Gtk.Button(label=_('Copy _URL'), use_underline=True)
        copy.add_css_class('pill')
        pages.connect_weak(copy, 'clicked', self._on_action_clicked,
                           'copy_url')
        buttons.append(copy)
        if actions.is_owner:
            edit = Gtk.Button(label=_('_Edit'), use_underline=True)
            edit.add_css_class('pill')
            pages.connect_weak(edit, 'clicked', self._on_action_clicked,
                               'edit')
            buttons.append(edit)
            delete = Gtk.Button(label=_('_Delete'), use_underline=True)
            delete.add_css_class('pill')
            delete.add_css_class('destructive-action')
            pages.connect_weak(delete, 'clicked', self._on_action_clicked,
                               'delete')
            buttons.append(delete)
        elif actions.my_item is not None:
            leave = Gtk.Button(label=_('Remove _Myself'),
                               use_underline=True)
            leave.add_css_class('pill')
            leave.add_css_class('destructive-action')
            pages.connect_weak(leave, 'clicked', self._on_action_clicked,
                               'remove_me')
            buttons.append(leave)
        column.append(buttons)

        members = [item for item in collection.get('items') or []
                   if item.get('state') != 'revoked']
        group = Adw.PreferencesGroup(title=ngettext(
            '%d Member', '%d Members', len(members)) % len(members))
        for item in members:
            account = by_id.get(item.get('account_id'))
            if account is None:
                continue
            group.add(self._member_row(account, item))
        details.append(group)

        owner = by_id.get(collection.get('account_id'))
        if owner is not None:
            created_by = Adw.PreferencesGroup(title=_('Created By'))
            created_by.add(pages.AccountRow(self.client, owner))
            details.append(created_by)

        if collection.get('sensitive'):
            warning = Adw.StatusPage(
                icon_name='dialog-warning-symbolic',
                title=_('Sensitive Collection'),
                description=_('The description and the accounts are '
                              'hidden behind a content warning.'))
            show = Gtk.Button(label=_('_Show Content'), use_underline=True,
                              halign=Gtk.Align.CENTER)
            show.add_css_class('pill')
            warning.set_child(show)
            details.set_visible(False)
            self._hidden = (details, warning)
            pages.connect_weak(show, 'clicked', self._on_show_content)
            column.append(warning)
        column.append(details)

        clamp = Adw.Clamp(maximum_size=pages.COLUMN_WIDTH, child=column)
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                      vexpand=True, child=clamp)
        self._body = scroller
        self.stack.add_named(scroller, 'content')
        self.stack.set_visible_child_name('content')

    def _member_row(self, account, item):
        row = pages.AccountRow(self.client, account)
        if item.get('state') == 'pending':
            badge = Gtk.Label(label=_('Pending'), valign=Gtk.Align.CENTER)
            badge.add_css_class('caption-heading')
            badge.add_css_class('warning')
            row.add_suffix(badge)
        return row

    def _on_tag_clicked(self, _button, name):
        self._open_tag(name)

    def _on_action_clicked(self, _button, method):
        getattr(self._actions, method)()

    def _on_show_content(self, _button):
        details, warning = self._hidden
        details.set_visible(True)
        warning.set_visible(False)

    def _open_tag(self, name):
        from .hashtags import push_hashtag
        window = pages.window_of(self)
        if window is not None:
            push_hashtag(self, self.client, name)

    def _pop(self):
        window = pages.window_of(self)
        if window is not None:
            window.navigation_view.pop()


# Create / edit

class MemberRow(Adw.ActionRow):
    """An account in the editor: Add/Remove button, pending badge."""

    def __init__(self, account, **kwargs):
        super().__init__(activatable=False, use_markup=False, **kwargs)
        from .timeline import AvatarCache
        name = pages.display_name(account)
        self.set_title(name)
        self.set_subtitle('@' + account.get('acct', ''))
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, account)
        self.add_prefix(avatar)

    def add_badge(self, text):
        badge = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
        badge.add_css_class('caption-heading')
        badge.add_css_class('warning')
        self.add_suffix(badge)


def can_invite(account):
    approval = account.get('feature_approval') or {}
    return approval.get('current_user') in ('automatic', 'manual')


class CollectionEditDialog(Adw.Dialog):
    """Create (``collection`` None) or edit a collection. In edit mode the
    member changes are applied as they are made; the details are saved
    with the Save button."""

    def __init__(self, client, collection=None, on_saved=None, **kwargs):
        super().__init__(content_width=520, content_height=720, **kwargs)
        self.client = client
        self.collection = collection
        self.edit_mode = collection is not None
        self.on_saved = on_saved
        self.members = {}
        self._search_id = 0
        self._search_results = []
        self.set_title(_('Edit Collection') if self.edit_mode
                       else _('New Collection'))

        self.page = Adw.PreferencesPage()
        self.toasts = Adw.ToastOverlay(child=self.page)
        toolbar = Adw.ToolbarView(content=self.toasts)
        header = Adw.HeaderBar()
        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        pages.connect_weak(cancel, 'clicked', self._on_cancel)
        header.pack_start(cancel)
        self.save_button = Gtk.Button(
            label=_('_Save') if self.edit_mode else _('_Create'),
            use_underline=True)
        self.save_button.add_css_class('suggested-action')
        pages.connect_weak(self.save_button, 'clicked', self._on_save)
        header.pack_end(self.save_button)
        toolbar.add_top_bar(header)
        self.set_child(toolbar)

        details = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title=_('Name'))
        self._limit_length(self.name_row, 40)
        pages.connect_weak(self.name_row, 'changed', self._update_save)
        self.description_row = Adw.EntryRow(title=_('Description'))
        self._limit_length(self.description_row, 100)
        self.topic_row = Adw.EntryRow(title=_('Topic (Hashtag)'))
        self._limit_length(self.topic_row, 100)
        languages = language_options()
        self.language_codes = [code for code, _n in languages]
        self.language_row = Adw.ComboRow(
            title=_('Language'), enable_search=True,
            expression=Gtk.PropertyExpression.new(
                Gtk.StringObject, None, 'string'),
            model=Gtk.StringList.new([name for _c, name in languages]))
        code = default_language_code(collection)
        if code in self.language_codes:
            self.language_row.set_selected(self.language_codes.index(code))
        for row in (self.name_row, self.description_row, self.language_row,
                    self.topic_row):
            details.add(row)
        self.page.add(details)

        visibility = Adw.PreferencesGroup(title=_('Visibility'))
        self.public_check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.unlisted_check = Gtk.CheckButton(group=self.public_check,
                                              valign=Gtk.Align.CENTER)
        public = Adw.ActionRow(
            title=_('Public'), activatable_widget=self.public_check,
            subtitle=_('Discoverable in search results and other areas '
                       'where recommendations appear.'))
        public.add_prefix(self.public_check)
        unlisted = Adw.ActionRow(
            title=_('Unlisted'), activatable_widget=self.unlisted_check,
            subtitle=_('Visible to anyone with a link. Hidden from search '
                       'results and recommendations.'))
        unlisted.add_prefix(self.unlisted_check)
        visibility.add(public)
        visibility.add(unlisted)
        self.page.add(visibility)

        warning = Adw.PreferencesGroup(title=_('Content Warning'))
        self.sensitive_row = Adw.SwitchRow(
            title=_('Mark as Sensitive'),
            subtitle=_("Hides the collection's description and accounts "
                       'behind a content warning. The name stays '
                       'visible.'))
        warning.add(self.sensitive_row)
        self.page.add(warning)

        self.search_group = Adw.PreferencesGroup(
            title=_('Add Accounts'),
            description=_('Only people who allow being featured can be '
                          'added.'))
        self.search_row = Adw.EntryRow(title=_('Search Accounts'))
        pages.connect_weak(self.search_row, 'changed',
                           self._on_search_changed)
        self.search_group.add(self.search_row)
        self.page.add(self.search_group)
        self.members_group = Adw.PreferencesGroup()
        self.page.add(self.members_group)

        if self.edit_mode:
            self.name_row.set_text(collection.get('name', ''))
            self.description_row.set_text(collection.get('description')
                                          or '')
            tag = (collection.get('tag') or {}).get('name')
            self.topic_row.set_text('#' + tag.lstrip('#') if tag else '')
            discoverable = bool(collection.get('discoverable'))
            self.sensitive_row.set_active(bool(collection.get('sensitive')))
            self._load_members()
        else:
            discoverable = True
        self.public_check.set_active(discoverable)
        self.unlisted_check.set_active(not discoverable)
        self._update_save()
        self._update_members_title()

    @staticmethod
    def _limit_length(row, length):
        delegate = row.get_delegate()
        if delegate is not None and hasattr(delegate, 'set_max_length'):
            delegate.set_max_length(length)

    def _on_cancel(self, _button):
        self.close()

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, use_markup=False,
                                        timeout=5))

    def error(self, heading, error):
        self.toast('%s: %s' % (heading, summarize_error(
            getattr(error, 'message', str(error)))))

    def _update_save(self, *_args):
        self.save_button.set_sensitive(
            bool(self.name_row.get_text().strip()))

    def _update_members_title(self):
        self.members_group.set_title(
            _('Members (%(count)d/%(max)d)') % {
                'count': len(self.members), 'max': MAX_MEMBERS})
        self.members_group.set_visible(bool(self.members) or self.edit_mode)

    # Members

    def _load_members(self):
        def ok(data):
            for account in data.get('accounts') or []:
                remember_account(self.client, account)
            by_id = {a['id']: a for a in data.get('accounts') or []}
            owner = (data.get('collection') or {}).get('account_id')
            for item in (data.get('collection') or {}).get('items') or []:
                account = by_id.get(item.get('account_id'))
                if account is None or item.get('state') == 'revoked' \
                        or account['id'] == owner:
                    continue
                self._add_member(account, item)

        self.members_group.set_sensitive(False)
        _send(self.client, 'GET', '/collections/%s' % self.collection['id'],
              lambda data: (ok(data),
                            self.members_group.set_sensitive(True)),
              lambda e: (self.error(_('Could not load the members'), e),
                         self.members_group.set_sensitive(True)))

    def _add_member(self, account, item=None):
        if account['id'] in self.members or len(self.members) >= MAX_MEMBERS:
            return
        row = MemberRow(account)
        if item and item.get('state') == 'pending':
            row.add_badge(_('Pending'))
        remove = pages.icon_button('user-trash-symbolic', _('Remove'))
        pages.connect_weak(remove, 'clicked', self._on_remove_clicked,
                           account)
        remove.add_css_class('error')
        row.add_suffix(remove)
        self.members[account['id']] = {'row': row, 'item': item}
        self.members_group.add(row)
        self._update_members_title()
        self._refresh_results()

    def _on_remove_clicked(self, _button, account):
        self._remove(account)

    def _remove_member(self, account_id):
        entry = self.members.pop(account_id, None)
        if entry is not None:
            self.members_group.remove(entry['row'])
        self._update_members_title()
        self._refresh_results()

    def _remove(self, account):
        entry = self.members.get(account['id'])
        if entry is None:
            return
        if not self.edit_mode or not entry.get('item'):
            self._remove_member(account['id'])
            return
        entry['row'].set_sensitive(False)
        _send(self.client, 'DELETE',
              '/collections/%s/items/%s' % (self.collection['id'],
                                            entry['item']['id']),
              lambda _d: self._remove_member(account['id']),
              lambda e: (entry['row'].set_sensitive(True),
                         self.error(_('Could not remove the account'), e)))

    def _invite(self, account, row):
        if not self.edit_mode:
            self._add_member(account)
            return
        row.set_sensitive(False)

        def ok(data):
            row.set_sensitive(True)
            self._add_member(account, data.get('collection_item'))

        _send(self.client, 'POST',
              '/collections/%s/items' % self.collection['id'], ok,
              lambda e: (row.set_sensitive(True),
                         self.error(_('Could not add the account'), e)),
              json_body={'account_id': account['id']})

    def _on_search_changed(self, *_args):
        self._search_id += 1
        search_id = self._search_id
        GLib.timeout_add(SEARCH_DELAY_MS, self._run_search, search_id)

    def _run_search(self, search_id):
        if search_id != self._search_id:
            return GLib.SOURCE_REMOVE
        query = self.search_row.get_text().strip()
        self._clear_results()
        if not query:
            return GLib.SOURCE_REMOVE

        def ok(data):
            if search_id != self._search_id:
                return
            self._clear_results()
            for account in data or []:
                remember_account(self.client, account)
                self._add_result(account)

        _send(self.client, 'GET', '/accounts/search', ok,
              lambda e: self.error(_('Search failed'), e),
              params={'q': query, 'resolve': 'false', 'limit': 5})
        return GLib.SOURCE_REMOVE

    def _clear_results(self):
        for row in self._search_results:
            self.search_group.remove(row)
        self._search_results = []

    def _add_result(self, account):
        row = MemberRow(account)
        row.account_data = account
        button = Gtk.Button(label=_('_Add'), use_underline=True,
                            valign=Gtk.Align.CENTER)
        pages.connect_weak(button, 'clicked', self._on_add_clicked)
        row.add_suffix(button)
        row.add_button = button
        self._search_results.append(row)
        self.search_group.add(row)
        self._refresh_result(row)

    def _on_add_clicked(self, button):
        row = next(r for r in self._search_results if r.add_button is button)
        self._invite(row.account_data, row)

    def _refresh_results(self):
        for row in self._search_results:
            self._refresh_result(row)

    def _refresh_result(self, row):
        account = row.account_data
        member = account['id'] in self.members
        invitable = can_invite(account)
        row.add_button.set_sensitive(
            invitable and not member and len(self.members) < MAX_MEMBERS)
        row.add_button.set_label(_('Added') if member else _('_Add'))
        row.add_button.set_use_underline(not member)
        row.set_sensitive(invitable or member)
        if not invitable and not member:
            row.set_tooltip_text(
                _('This account does not allow being featured'))

    # Saving

    def _body(self):
        body = {
            'name': self.name_row.get_text().strip(),
            'description': self.description_row.get_text().strip(),
            'language': self.language_codes[self.language_row.get_selected()]
            if self.language_row.get_selected() != Gtk.INVALID_LIST_POSITION
            else 'en',
            'sensitive': self.sensitive_row.get_active(),
            'discoverable': self.public_check.get_active(),
        }
        topic = self.topic_row.get_text().strip()
        if topic and topic != '#':
            body['tag_name'] = topic if topic.startswith('#') \
                else '#' + topic
        return body

    def _on_save(self, _button):
        body = self._body()
        if not body['name']:
            self.name_row.grab_focus()
            self.toast(_('The name cannot be empty'))
            return
        self.page.set_sensitive(False)

        def ok(_data):
            if self.on_saved is not None:
                self.on_saved()
            self.close()

        def failed(error):
            self.page.set_sensitive(True)
            self.error(_('Could not save the collection'), error)

        if self.edit_mode:
            original = self.collection
            changes = {key: value for key, value in body.items()
                       if value != self._original(original, key)}
            if not changes:
                ok(None)
                return
            _send(self.client, 'PATCH',
                  '/collections/%s' % original['id'], ok, failed,
                  json_body=changes)
        else:
            if not body['description']:
                del body['description']
            body['account_ids'] = list(self.members)
            _send(self.client, 'POST', '/collections', ok, failed,
                  json_body=body)

    @staticmethod
    def _original(collection, key):
        if key == 'tag_name':
            name = (collection.get('tag') or {}).get('name')
            return '#' + name.lstrip('#') if name else None
        if key == 'description':
            return collection.get('description') or ''
        return collection.get(key)
