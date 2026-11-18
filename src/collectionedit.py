# SPDX-License-Identifier: GPL-3.0-or-later
"""The create/edit dialog: name, description, language, topic, members."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import feedback, uihelpers  # noqa: E402
from .api import accounts as api_accounts  # noqa: E402
from .api import collections_api  # noqa: E402
from .collectionactions import can_invite  # noqa: E402
from .collectionbase import MAX_MEMBERS, SEARCH_DELAY_MS  # noqa: E402
from .collectionpage import MemberRow  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402
from .collectionbase import (_is_member, _send_api, default_language_code, language_options, remember_account)  # noqa: E402



class CollectionEditDialog(feedback.ToastDialog):
    """Create (``collection`` None) or edit a collection. In edit mode the
    member changes are applied as they are made; the details are saved
    with the Save button."""

    def __init__(self, client, collection=None, on_saved=None, **kwargs):
        super().__init__(_('Edit Collection') if collection is not None
                         else _('New Collection'), 520, 720, **kwargs)
        self.client = client
        self.collection = collection
        self.edit_mode = collection is not None
        self.on_saved = on_saved
        self.members = {}
        self._search_id = 0
        self._search_results = []

        cancel = Gtk.Button(label=_('_Cancel'), use_underline=True)
        connect_weak(cancel, 'clicked', self._on_cancel)
        self.header.pack_start(cancel)
        self.save_button = Gtk.Button(
            label=_('_Save') if self.edit_mode else _('_Create'),
            use_underline=True)
        self.save_button.add_css_class('suggested-action')
        connect_weak(self.save_button, 'clicked', self._on_save)
        self.header.pack_end(self.save_button)

        details = Adw.PreferencesGroup()
        self.name_row = Adw.EntryRow(title=_('Name'))
        self._limit_length(self.name_row, 40)
        connect_weak(self.name_row, 'changed', self._update_save)
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
        self.submit_on_enter(self.save_button, self.name_row,
                             self.description_row, self.topic_row)

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
        connect_weak(self.search_row, 'changed',
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
                if account is None or not _is_member(item) \
                        or account['id'] == owner:
                    continue
                self._add_member(account, item)

        self.members_group.set_sensitive(False)
        _send_api(self.client,
                  lambda cb: collections_api.get_async(
                      self.client, self.collection['id'], callback=cb),
                  collections_api.get_finish,
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
        remove = uihelpers.icon_button('user-trash-symbolic', _('Remove'))
        connect_weak(remove, 'clicked', self._on_remove_clicked,
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
        _send_api(
            self.client,
            lambda cb: collections_api.remove_item_async(
                self.client, self.collection['id'], entry['item']['id'],
                callback=cb),
            collections_api.remove_item_finish,
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

        _send_api(
            self.client,
            lambda cb: collections_api.add_item_async(
                self.client, self.collection['id'], account['id'],
                callback=cb),
            collections_api.add_item_finish,
            ok,
            lambda e: (row.set_sensitive(True),
                       self.error(_('Could not add the account'), e)))

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

        _send_api(
            self.client,
            lambda cb: api_accounts.search_async(
                self.client,
                {'q': query, 'resolve': 'false', 'limit': 5}, callback=cb),
            api_accounts.search_finish,
            ok, lambda e: self.error(_('Search failed'), e))
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
        connect_weak(button, 'clicked', self._on_add_clicked)
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
        else:
            # Nothing typed: no topic. `tag_name: null` is how an edit
            # removes one (Collection#tag_name= with nil unsets the tag);
            # a create leaves the key out.
            body['tag_name'] = None
        return body

    def _on_save(self, _button):
        body = self._body()
        if not body['name']:
            self.name_row.grab_focus()
            self.toast(_('The name cannot be empty'))
            return
        # The header is outside the page: without this a second click
        # while the request runs creates the collection twice.
        self.save_button.set_sensitive(False)
        self.page.set_sensitive(False)

        def ok(_data):
            if self.on_saved is not None:
                self.on_saved()
            self.close()

        def failed(error):
            self.page.set_sensitive(True)
            self._update_save()
            self.error(_('Could not save the collection'), error)

        if self.edit_mode:
            original = self.collection
            changes = {key: value for key, value in body.items()
                       if value != self._original(original, key)}
            if not changes:
                ok(None)
                return
            _send_api(self.client,
                      lambda cb: collections_api.update_async(
                          self.client, original['id'], changes,
                          callback=cb),
                      collections_api.update_finish,
                      ok, failed)
        else:
            if not body['description']:
                del body['description']
            if body['tag_name'] is None:
                del body['tag_name']
            body['account_ids'] = list(self.members)
            _send_api(self.client,
                      lambda cb: collections_api.create_async(
                          self.client, body, callback=cb),
                      collections_api.create_finish,
                      ok, failed)

    @staticmethod
    def _original(collection, key):
        if key == 'tag_name':
            name = (collection.get('tag') or {}).get('name')
            return '#' + name.lstrip('#') if name else None
        if key == 'description':
            return collection.get('description') or ''
        return collection.get(key)
