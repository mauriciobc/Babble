# SPDX-License-Identifier: GPL-3.0-or-later
"""Collections: the feature bar, the rows and the collections list.

The actions a collection page offers, the avatar stack and the two rows
that stand for a collection, over the list page that holds them.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import collectionbase  # noqa: E402
from . import accountrows, feedback, pagenav, pages, uihelpers  # noqa: E402
from .api import accounts as api_accounts  # noqa: E402
from .api import collections_api  # noqa: E402
from .collectionbase import _is_member, _items  # noqa: E402
from .i18n import gettext as _, ngettext  # noqa: E402
from .mastodon import summarize_error_markup  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402
from .collectionbase import (_send_api,
    collection_limit,
    format_date,
    owner_label)  # noqa: E402



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
        self.me = collectionbase._current_account(widget).get('id')
        self.is_owner = collection.get('account_id') == self.me
        self.my_item = next(
            (item for item in collection.get('items') or []
             if item.get('account_id') == self.me
             and _is_member(item)), None)

    def menu_button(self):
        group = Gio.SimpleActionGroup()
        menu = Gio.Menu()
        entries = [('copy-url', _('Copy URL'))]
        if self.is_owner:
            entries.append(('edit', _('Edit')))
        if self.my_item is not None and not self.is_owner:
            entries.append(('remove-me', _('Remove Myself')))
        if not self.is_owner:
            entries.append(('report', _('Report Collection')))
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
                                valign=Gtk.Align.CENTER, menu_model=menu)
        button.add_css_class('flat')
        uihelpers.set_accessible_label(button, _('More Options'))
        button.insert_action_group('collection', group)
        return button

    def copy_url(self):
        url = self.collection.get('url') or self.collection.get('uri')
        if not url:
            return
        self.widget.get_clipboard().set(url)
        feedback.show_toast(self.widget, _('Copied URL to clipboard'))

    def edit(self):
        from .collectionedit import CollectionEditDialog
        dialog = CollectionEditDialog(self.client, self.collection,
                                      on_saved=self.on_changed)
        dialog.present(self.widget)

    def report(self):
        """Report the Collection to its owner's server.

        The reported account is the Collection's owner; the Collection
        itself rides along as ``collection_ids[]`` evidence (Mastodon
        4.6+)."""
        from .report import present_report
        owner_id = self.collection.get('account_id')
        if not owner_id:
            return

        def with_owner(accounts):
            owner = accounts.get(owner_id)
            if owner is None:
                return
            present_report(self.widget, self.client, owner,
                           collection=self.collection)

        collectionbase.fetch_accounts(self.client, [owner_id], with_owner,
                       widget=self.widget)

    def remove_me(self):
        def go():
            _send_api(
                self.client,
                lambda cb: collections_api.revoke_item_async(
                    self.client, self.collection['id'], self.my_item['id'],
                    callback=cb),
                collections_api.revoke_item_finish,
                lambda _d: self._gone(_('You were removed from the '
                                          'collection')),
                  lambda e: feedback.report_error(
                      self.widget, _('Could not remove you'), e))

        feedback.confirm(
            self.widget,
            _('Remove Yourself from “%s”?') % self.collection.get('name', ''),
            _('You will no longer be featured in this collection.'),
            _('_Remove'), go)

    def delete(self):
        def go():
            _send_api(
                self.client,
                lambda cb: collections_api.delete_async(
                    self.client, self.collection['id'], callback=cb),
                collections_api.delete_finish,
                lambda _d: self._gone(_('Collection deleted')),
                  lambda e: feedback.report_error(
                      self.widget, _('Could not delete the collection'), e))

        feedback.confirm(
            self.widget,
            _('Delete “%s”?') % self.collection.get('name', ''),
            _('The accounts in it are not affected.'), _('_Delete'), go)

    def _gone(self, message):
        feedback.show_toast(self.widget, message)
        self.on_gone()

class AvatarStack(Gtk.Box):
    """Up to three member avatars (a placeholder for sensitive
    collections), filled once their accounts are fetched."""

    def __init__(self, client, collection, **kwargs):
        super().__init__(spacing=2, valign=Gtk.Align.CENTER,
                         accessible_role=Gtk.AccessibleRole.PRESENTATION,
                         **kwargs)
        items = [item for item in collection.get('items') or []
                 if _is_member(item)]
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
            collectionbase.fetch_accounts(client, list(self.avatars), self._fill,
                           widget=self)

    def _fill(self, accounts):
        from .mediacache import AvatarCache
        for account_id, avatar in self.avatars.items():
            account = accounts.get(account_id)
            if account is None:
                continue
            avatar.set_text(accountrows.display_name(account))
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
            collectionbase.fetch_accounts(client, [collection.get('account_id')],
                           self._set_owner, widget=self)
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
        from .collectionpage import CollectionPage
        pagenav.push_page(
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
        account = collectionbase._current_account(self)
        self.me = account.get('id')
        self.limit = collection_limit(account)
        self._own_count = 0

        self.stack = Adw.ViewStack()
        self.title_widget = Adw.InlineViewSwitcher(
            stack=self.stack, display_mode=Adw.InlineViewSwitcherDisplayMode.LABELS)
        self.new_button = uihelpers.icon_button(
            'list-add-symbolic', _('New Collection'), flat=False)
        connect_weak(self.new_button, 'clicked', self._on_new)
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

        _send_api(self.client,
                  lambda cb: api_accounts.verify_credentials_async(
                      self.client, callback=cb),
                  api_accounts.verify_credentials_finish,
                  ok, failed)

    def _build(self):
        # The lists and their rows live inside the page: hand them weak
        # callbacks so they do not keep the page alive through C.
        self.own = pages.PagedList(
            self.client, collections_api.list_path('by_account', self.me),
            weak_method(self._make_row), empty_title=_('No Collections'),
            empty_icon='folder-symbolic',
            empty_description=_('Collections you create to recommend '
                                'accounts show up here.'),
            extract=weak_method(self._extract_own))
        self.featuring = pages.PagedList(
            self.client, collections_api.list_path('featuring', self.me),
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
        from .collectionedit import CollectionEditDialog
        dialog = CollectionEditDialog(self.client, None,
                                      on_saved=self.refresh)
        dialog.present(self)

    def refresh(self):
        if self.own is not None:
            self.own.refresh()
        if self.featuring is not None:
            self.featuring.refresh()

def can_invite(account):
    approval = account.get('feature_approval') or {}
    return approval.get('current_user') in ('automatic', 'manual')
