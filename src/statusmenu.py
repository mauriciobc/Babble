# SPDX-License-Identifier: GPL-3.0-or-later


import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Soup', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('Graphene', '1.0')

from .i18n import gettext as _

from gi.repository import (GLib, GObject, Gio,  # noqa: E402
                           Gtk)

from . import pages  # noqa: E402
from .pages import connect_weak  # noqa: E402
from .textemoji import (launch_uri,  # noqa: E402
                        )

"""The overflow menu on a post."""
from .statusactions import (
    WINDOW_CLIENT, StatusActions, _PostBound, _merge_status,
    _open_composer, _open_status_menu, _open_thread)
from .timelinebase import (
    QUOTE_POLICIES, VISIBILITY_INFO, _flag, _own_account_id,
    _translation_enabled, can_quote, quote_policy_of, ui_language)


class StatusMenuButton(_PostBound, Gtk.MenuButton):
    """The '⋯' button of a post and its menu, bound to a StatusItem:
    StatusRow's overflow menu, usable on its own.

    The actions and the menu are built when the menu opens: a list binds
    rows far more often than anyone opens their menu. *actions* is the
    post's StatusActions; its buttons carry the toggle state, the menu's
    icon row runs their handlers and shows when that bar is hidden.

    Actions that change the post in place emit ``refresh`` (the host
    redraws it); ``translated`` follows it once a translation arrived,
    and ``deleted`` when the post is gone."""

    __gtype_name__ = 'BabbleStatusMenuButton'
    __gsignals__ = {
        'refresh': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'translated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'deleted': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    MENU_ACTIONS = (
        'reply', 'quote', 'view', 'open-browser', 'copy', 'stats',
        'history', 'translate', 'mute-conversation', 'pin', 'edit',
        'delete', 'redraft', 'revoke-quote', 'report', 'mention', 'mute',
        'block')
    # Stateful: the menu's icon row shows them pressed when set.
    MENU_TOGGLES = ('boost', 'favourite', 'bookmark')

    def __init__(self, actions=None, button_classes=('flat',), **kwargs):
        super().__init__(icon_name='view-more-symbolic',
                         tooltip_text=_('More Options'),
                         valign=Gtk.Align.CENTER, **kwargs)
        for css in button_classes:
            self.add_css_class(css)
        self.update_property([Gtk.AccessibleProperty.LABEL],
                             [_('More Options')])
        self.actions = actions
        self._menu_actions = None
        self.set_create_popup_func(_open_status_menu, self.weak_ref())

    def bind(self, item, client=WINDOW_CLIENT):
        self.item = item
        self._client = client
        if self.get_menu_model() is not None:
            # Rebuilt for this post the next time the menu opens.
            self.set_menu_model(None)

    def _refresh(self):
        self._notify('refresh')

    def _ensure_menu_actions(self):
        if self._menu_actions is not None:
            return
        group = Gio.SimpleActionGroup()
        self._menu_actions = {}
        for name in self.MENU_ACTIONS:
            action = Gio.SimpleAction.new(name, None)
            handler = getattr(self, '_menu_' + name.replace('-', '_'))
            connect_weak(action, 'activate', handler)
            group.add_action(action)
            self._menu_actions[name] = action
        for name in self.MENU_TOGGLES:
            action = Gio.SimpleAction.new_stateful(
                name, None, GLib.Variant.new_boolean(False))
            connect_weak(action, 'activate',
                         getattr(self, '_menu_' + name))
            group.add_action(action)
            self._menu_actions[name] = action
        policy = Gio.SimpleAction.new_stateful(
            'quote-policy', GLib.VariantType.new('s'),
            GLib.Variant.new_string('public'))
        connect_weak(policy, 'activate', self._menu_quote_policy)
        group.add_action(policy)
        self._menu_actions['quote-policy'] = policy
        self.insert_action_group('row', group)

    def prepare(self):
        """Actions and menu for the bound post, as the menu opens."""
        if self.item is None:
            return
        self._ensure_menu_actions()
        self._sync_menu_state(self.item)
        self.set_menu_model(self._build_menu(self.item))

    def _can_mute_conversation(self):
        status = self.item.status
        own = _own_account_id(self)
        if not own or not self._can_act():
            return False
        if self._is_own():
            return True
        return any(m.get('id') == own for m in status.get('mentions') or [])

    def _can_translate(self):
        status = self.item.status
        language = status.get('language')
        return bool(
            self._can_act() and not self._is_own() and language
            and language.lower().split('-')[0] != ui_language()
            and status.get('visibility') in ('public', 'unlisted')
            and _translation_enabled(self.client))

    def _revocable_quote(self):
        quote = self.item.status.get('quote') or {}
        quoted = quote.get('quoted_status')
        own = _own_account_id(self)
        return bool(
            quote.get('state') == 'accepted' and quoted and own
            and (quoted.get('account') or {}).get('id') == own
            and not self._is_own())

    def _build_menu(self, item):
        status = item.status
        own = self._is_own()
        signed_in = self._can_act()
        # Underscores in a handle would read as access keys.
        handle = item.handle.replace('_', '__')
        menu = Gio.Menu()
        # Phanpy keeps the post actions in this menu when the card has
        # no action bar: an icon row above the rest.
        if signed_in and self.actions is not None \
                and not self.actions.get_visible():
            menu.append_item(self._action_section(status))

        links = Gio.Menu()
        links.append(_('_View Post'), 'row.view')
        links.append(_('Open in _Browser'), 'row.open-browser')
        links.append(_('_Copy Link'), 'row.copy')
        if status.get('visibility') != 'direct' or own:
            links.append(_('View S_tats'), 'row.stats')
        if status.get('edited_at'):
            links.append(_('View Edit _History'), 'row.history')
        menu.append_section(None, links)

        post = Gio.Menu()
        if self._can_translate():
            # Not _Translate: View S_tats already takes T in this menu.
            post.append(_('Show _Original') if item.translation
                        else _('Tr_anslate'), 'row.translate')
        if self._can_mute_conversation():
            post.append(_('Un_mute Conversation') if status.get('muted')
                        else _('_Mute Conversation'),
                        'row.mute-conversation')
        if own and status.get('visibility') in ('public', 'unlisted'):
            post.append(_('_Unpin From Profile') if status.get('pinned')
                        else _('_Pin to Profile'), 'row.pin')
        if own and status.get('quote_approval') \
                and status.get('visibility') in ('public', 'unlisted'):
            submenu = Gio.Menu()
            for value, title in QUOTE_POLICIES:
                entry = Gio.MenuItem.new(title, None)
                entry.set_action_and_target_value(
                    'row.quote-policy', GLib.Variant.new_string(value))
                submenu.append_item(entry)
            post.append_submenu(_('Who Can _Quote'), submenu)
        if signed_in and self._revocable_quote():
            # Not _Remove…: Delete and _Redraft takes R in the same menu.
            post.append(_('Remove My Post _From Quote'), 'row.revoke-quote')
        if post.get_n_items():
            menu.append_section(None, post)

        if own:
            mine = Gio.Menu()
            mine.append(_('_Edit'), 'row.edit')
            mine.append(_('Delete and _Redraft'), 'row.redraft')
            mine.append(_('_Delete'), 'row.delete')
            menu.append_section(None, mine)
        elif signed_in:
            account = Gio.Menu()
            # Access keys are unique across the whole open menu, not per
            # section: _Mute Conversation (M), Open in _Browser (B) and
            # Show _Original (O) already hold those letters in it.
            account.append(_('Ment_ion %s') % handle, 'row.mention')
            account.append(_('M_ute %s') % handle, 'row.mute')
            account.append(_('Bloc_k %s') % handle, 'row.block')
            account.append(_('_Report Post…'), 'row.report')
            menu.append_section(None, account)
        return menu

    def _action_section(self, status):
        """Reply/Boost/(Quote)/Favorite/Bookmark as a row of icon
        buttons; the toggles render pressed from their action state."""
        section = Gio.Menu()
        # Pinned here rather than looked up in the module globals at call
        # time, like StatusRow pins its own: the menu must not depend on
        # the name still resolving when the user opens it.
        icons = StatusActions
        entries = [(_('Reply'), 'row.reply', icons.ICON_REPLY),
                   (_('Boost'), 'row.boost', icons.ICON_BOOST)]
        if 'quote_approval' in status:
            entries.append((_('Quote'), 'row.quote', icons.ICON_QUOTE))
        entries += [(_('Favorite'), 'row.favourite', icons.ICON_FAVOURITE[0]),
                    (_('Bookmark'), 'row.bookmark', icons.ICON_BOOKMARK[0])]
        for label, action, icon in entries:
            entry = Gio.MenuItem.new(label, action)
            entry.set_attribute_value('verb-icon',
                                      GLib.Variant.new_string(icon))
            section.append_item(entry)
        item = Gio.MenuItem.new_section(None, section)
        item.set_attribute_value(
            'display-hint', GLib.Variant.new_string('horizontal-buttons'))
        return item

    def _author(self):
        if self.item is None or not self._can_act():
            return None
        account = self.item.status['account']
        return account if account.get('id') else None

    def _toast(self, text, undo=None):
        pages.show_toast(self, text, undo=undo)

    # The menu's icon row runs the action bar's handlers, so reminders,
    # the boost dialog and the optimistic rollback stay in one place.
    # The bar's (possibly hidden) buttons carry the toggle state.

    def _menu_reply(self, *_args):
        if self.actions is not None:
            self.actions.reply()

    def _menu_quote(self, *_args):
        if self.actions is not None:
            self.actions.quote()

    def _menu_boost(self, *_args):
        if self.item is not None and self.actions is not None:
            self.actions.boost()

    def _menu_favourite(self, *_args):
        if self.item is not None and self.actions is not None:
            self.actions.favourite()

    def _menu_bookmark(self, *_args):
        if self.item is not None and self.actions is not None:
            self.actions.bookmark()

    def _menu_view(self, *_args):
        if self.item is not None:
            _open_thread(self, self.client, self.item)

    def _menu_open_browser(self, *_args):
        if self.item is not None and self.item.status_url:
            launch_uri(self, self.item.status_url)

    def _menu_copy(self, *_args):
        if self.item is None or not self.item.status_url:
            return
        status = self.item.status
        visibility = status.get('visibility')
        if visibility in ('direct', 'private') \
                and _flag('copy-private-link-reminder'):
            self._confirm_copy(status)
            return
        self._copy_url()

    def _copy_url(self):
        clipboard = self.get_clipboard()
        if clipboard and self.item is not None:
            clipboard.set(self.item.status_url)
            self._toast(_('Link copied'))

    def _confirm_copy(self, status):
        visibility = status.get('visibility')
        label = VISIBILITY_INFO[visibility][0]
        if visibility == 'direct':
            detail = _('Only mentioned people will be able to view it.')
        elif self._is_own():
            detail = _('Only mentioned people and your followers will be '
                       'able to view it.')
        else:
            detail = _('Only mentioned people and people that follow %s '
                       'will be able to view it.') % self.item.handle
        pages.confirm_remind(
            self, _('Copy “%s” Post Link?') % label, detail,
            'copy', _('_Copy'), 'copy-private-link-reminder', self._copy_url)

    def _menu_stats(self, *_args):
        if self.item is None or self.client is None:
            return
        from .statusviews import push_status_stats
        push_status_stats(self, self.client, self.item.status,
                          'favourites')

    def show_history(self):
        if self.item is None or self.client is None:
            return
        from .statusviews import push_edit_history
        push_edit_history(self, self.client, self.item.status)

    def _menu_history(self, *_args):
        self.show_history()

    def toggle_translation(self, *_args):
        """Translate the post, or drop the translation. Public because
        StatusRow's translation box has a "Show _Original" button that
        does the same thing; the refresh and the ``translated`` note go
        to the host through ``_notify``, so both callers redraw. Takes
        and ignores arguments so it can be connected as a handler."""
        item = self.item
        if item is None:
            return
        if item.translation:
            item.translation = None
            self._refresh()
            return

        def translated(data):
            item.translation = data
            if self.item is item:
                self._refresh()
                self._notify('translated')

        pages.call(self, self.client, 'POST',
                   '/statuses/%s/translate' % item.status_id, translated,
                   _('Could not translate'),
                   form={'lang': ui_language()})

    def _menu_translate(self, *_args):
        self.toggle_translation()

    def _post_action(self, action, heading):
        """POST /statuses/:id/<action> and merge the returned status."""
        item = self.item

        def done(data):
            _merge_status(item, data)
            if self.item is item:
                self._refresh()

        pages.call(self, self.client, 'POST',
                   '/statuses/%s/%s' % (item.status_id, action), done,
                   heading)

    def _menu_mute_conversation(self, *_args):
        if self.item is None:
            return
        muted = bool(self.item.status.get('muted'))
        self._post_action(
            'unmute' if muted else 'mute',
            _('Could not unmute conversation') if muted
            else _('Could not mute conversation'))

    def _menu_pin(self, *_args):
        if self.item is None:
            return
        pinned = bool(self.item.status.get('pinned'))
        self._post_action('unpin' if pinned else 'pin',
                          _('Could not unpin post') if pinned
                          else _('Could not pin post'))

    def _menu_quote_policy(self, action, parameter):
        item = self.item
        if item is None or parameter is None:
            return
        value = parameter.get_string()

        def done(data):
            _merge_status(item, data)
            if self.item is item:
                self._refresh()

        pages.call(self, self.client, 'PUT',
                   '/statuses/%s/interaction_policy' % item.status_id, done,
                   _('Could not change quote policy'),
                   form={'quote_approval_policy': value})

    def _menu_revoke_quote(self, *_args):
        item = self.item
        if item is None:
            return
        quoted = item.status['quote']['quoted_status']

        def revoke():
            def done(data):
                _merge_status(item, data)
                if self.item is item:
                    self._refresh()
                self._toast(_('Your post was removed from the quote'))

            pages.call(self, self.client, 'POST',
                       '/statuses/%s/quotes/%s/revoke'
                       % (quoted['id'], item.status_id), done,
                       _('Could not remove your post'))

        pages.confirm(self, _('Remove Your Post From the Quote?'),
                      _('The quote will no longer show your post.'),
                      _('_Remove'), revoke)

    def _menu_edit(self, *_args):
        item = self.item
        if item is None:
            return

        def loaded(source):
            status = dict(item.status)
            status['text'] = source.get('text', '')
            if 'spoiler_text' in source:
                status['spoiler_text'] = source['spoiler_text']
            _open_composer(self, 'start_edit', status)

        pages.call(self, self.client, 'GET',
                   '/statuses/%s/source' % item.status_id, loaded,
                   _('Could not load post for editing'))

    def _menu_delete(self, *_args):
        item = self.item
        if item is None:
            return
        pages.confirm(
            self, _('Delete Post?'),
            _('This post will be removed from your profile and from the '
              'timelines of other servers.'),
            _('_Delete'), lambda: self._delete(item, None))

    def _menu_redraft(self, *_args):
        item = self.item
        if item is None:
            return
        pages.confirm(
            self, _('Delete and Redraft?'),
            _('This post will be deleted and its text, attachments and '
              'poll copied into a new draft.'),
            _('_Delete and Redraft'),
            lambda: self._delete(item, 'start_redraft'))

    def _delete(self, item, composer_method):
        """DELETE the post; for a redraft hand the returned source to
        the composer."""

        def done(data):
            self._after_deleted(item)
            if composer_method:
                status = dict(item.status)
                if isinstance(data, dict):
                    status.update(data)
                _open_composer(self, composer_method, status)
            else:
                self._toast(_('Post deleted'))

        pages.call(self, self.client, 'DELETE',
                   '/statuses/%s' % item.status_id, done,
                   _('Could not delete post'))

    def _after_deleted(self, item):
        widget = self.host() if self.host else self
        from .timelinepage import TimelinePage
        while widget is not None and not isinstance(widget, TimelinePage):
            widget = widget.get_parent()
        if widget is not None:
            widget.on_stream_delete(item.status_id)
        self._notify('deleted')

    def _menu_report(self, *_args):
        account = self._author()
        if account is None:
            return
        from .report import present_report
        present_report(self, self.client, account, self.item.status)

    def _menu_mention(self, *_args):
        account = self._author()
        if account is None:
            return
        window = pages.window_of(self)
        if window is None:
            return
        dialog = window.ensure_composer()
        if dialog is None:
            return
        dialog.start_mention(account)
        window.present_composer()

    def _menu_mute(self, *_args):
        account = self._author()
        if account is None:
            return
        client, handle = self.client, self.item.handle

        def muted(_data):
            def undo():
                pages.call(self, client, 'POST',
                           '/accounts/%s/unmute' % account['id'], None,
                           _('Unmute failed'))

            self._toast(_('Muted %s') % handle, undo)

        pages.call(self, client, 'POST', '/accounts/%s/mute' % account['id'],
                   muted, _('Mute failed'))

    def _menu_block(self, *_args):
        account = self._author()
        if account is None:
            return
        client, handle = self.client, self.item.handle

        def block():
            pages.call(self, client, 'POST',
                       '/accounts/%s/block' % account['id'],
                       lambda _data: self._toast(_('Blocked %s') % handle),
                       _('Block failed'))

        pages.confirm(
            self, _('Block %s?') % handle,
            _('They will no longer be able to follow your account or '
              'see your posts, and their posts will be hidden.'),
            _('_Block'), block)

    def _sync_menu_state(self, item):
        status = item.status
        can_act = self._can_act()
        self._menu_actions['open-browser'].set_enabled(
            bool(item.status_url))
        self._menu_actions['copy'].set_enabled(bool(item.status_url))
        for name in ('mute', 'block', 'report', 'mention'):
            self._menu_actions[name].set_enabled(can_act)
        self._menu_actions['quote-policy'].set_state(
            GLib.Variant.new_string(quote_policy_of(status)))
        idle = can_act and not (self.actions is not None
                                and self.actions.busy)
        boostable = item.reblogged or self.can_be_boosted(item)
        for name, active, enabled in (
                ('boost', item.reblogged, idle and boostable),
                ('favourite', item.favourited, idle),
                ('bookmark', item.bookmarked, idle)):
            action = self._menu_actions[name]
            action.set_state(GLib.Variant.new_boolean(bool(active)))
            action.set_enabled(enabled)
        self._menu_actions['quote'].set_enabled(
            can_act and can_quote(status))

