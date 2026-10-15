"""Report an account or post: a wizard in an Adw.Dialog.

Steps (Tuba's Dialogs/Report, Phanpy's report-modal): choose a category,
pick the violated server rules (violation only), pick supporting posts,
add a comment and send. POST /api/v1/reports, documented at
https://docs.joinmastodon.org/methods/reports/#post
"""

import re

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from .i18n import gettext as _, ngettext  # noqa: E402

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import pages  # noqa: E402
from .mastodon import summarize_error  # noqa: E402

COMMENT_MAX = 1000
EXCERPT_MAX = 200

CATEGORIES = ('spam', 'legal', 'violation', 'other')


def category_info(category):
    """(title, subtitle) of a report category."""
    return {
        'spam': (_('It’s Spam'),
                 _('Malicious links, fake engagement or repetitive replies')),
        'legal': (_('Illegal Content'),
                  _('Content that breaks the law of your or the server’s '
                    'country')),
        'violation': (_('Violates Server Rules'),
                      _('You are aware that it breaks specific rules')),
        'other': (_('Something Else'),
                  _('The issue does not fit into the other categories')),
    }[category]


def _unique(values):
    """String values in order, without repeats."""
    return list(dict.fromkeys(str(value) for value in values))


def build_report_payload(account_id, category, status_ids=(), rule_ids=(),
                         collection_ids=(), comment='', forward=None):
    """Form fields for POST /api/v1/reports.

    Array fields use the ``name[]`` key Rails needs to read several
    values; ``urlencode(doseq=True)`` turns the lists into repeated keys.
    ``rule_ids`` are sent for the ``violation`` category only (and then
    at least one is required), ``collection_ids`` attach Collection
    evidence (Mastodon 4.6+), ``comment`` only when non-blank and at
    most COMMENT_MAX characters, ``forward`` only when given (remote
    accounts) as the string 'true'/'false' (Rails would read 'False' as
    true). Raises ValueError for input the server would refuse."""
    if category not in CATEGORIES:
        raise ValueError('unknown report category: %r' % (category,))
    payload = {'account_id': str(account_id), 'category': category}
    seen = _unique(status_ids)
    if seen:
        payload['status_ids[]'] = seen
    collections = _unique(collection_ids)
    if collections:
        payload['collection_ids[]'] = collections
    if category == 'violation':
        rules = _unique(rule_ids)
        if not rules:
            raise ValueError('a violation report needs at least one rule')
        payload['rule_ids[]'] = rules
    comment = (comment or '').strip()
    if len(comment) > COMMENT_MAX:
        raise ValueError('comment longer than %d characters' % COMMENT_MAX)
    if comment:
        payload['comment'] = comment
    if forward is not None:
        payload['forward'] = 'true' if forward else 'false'
    return payload


def account_domain(account):
    """Domain of a remote account (acct 'user@host'), '' for local ones."""
    acct = account.get('acct') or ''
    return acct.split('@', 1)[1] if '@' in acct else ''


def status_excerpt(status):
    """One-line plain text of a post for the selection list."""
    from .statustext import parse_content
    parts = [segment['text'] for segment in
             parse_content(status.get('content') or '')]
    text = re.sub(r'\s+', ' ', ' '.join(parts)).strip()
    if len(text) > EXCERPT_MAX:
        text = text[:EXCERPT_MAX].rstrip() + '…'
    return text


def _plain_name(account):
    return re.sub(r':[A-Za-z0-9_+\-]+:', '',
                  pages.display_name(account)).strip() \
        or account.get('acct', '')


class _PostRow(Adw.ActionRow):
    """Compact post line with a check button."""

    def __init__(self, status):
        super().__init__(use_markup=False, title_lines=3, subtitle_lines=1)
        from .statustext import relative_time
        self.check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
        self.check.add_css_class('selection-mode')
        self.add_prefix(self.check)
        self.set_activatable_widget(self.check)

        excerpt = status_excerpt(status)
        self.set_title(excerpt or _('(No text)'))
        details = []
        when = relative_time(status.get('created_at') or '')
        if when:
            details.append(when)
        attachments = len(status.get('media_attachments') or [])
        if attachments:
            details.append(ngettext('%d attachment', '%d attachments',
                                    attachments) % attachments)
        if status.get('spoiler_text'):
            details.append(_('Content warning'))
        self.set_subtitle(' · '.join(details))


class ReportDialog(Adw.Dialog):
    """Four-step report wizard (the violated-rules step only for the
    ``violation`` category). Sends through ``client.request_async``;
    success closes the dialog and toasts ‘Report sent’ on the window, a
    failure stays on the last step with an error toast."""

    __gtype_name__ = 'BabbleReportDialog'

    def __init__(self, client, account, status=None, parent_widget=None,
                 collection=None):
        super().__init__(content_width=480, content_height=600,
                         title=_('Report @%s') % account.get('acct', ''))
        self.client = client
        self.account = account
        self.status = status
        self.collection = collection
        self.collection_ids = [str(collection['id'])] if collection else []
        self.parent_widget = parent_widget
        self.domain = account_domain(account)
        self.category = None
        self.category_checks = {}
        self.category_rows = {}
        self.rule_checks = {}
        self.status_checks = {}
        self.next_buttons = {}
        self.nav_pages = {}
        self.posts_list = None
        self.forward_row = None
        self._sending = False

        self.overlay = Adw.ToastOverlay()
        self.nav = Adw.NavigationView()
        self.overlay.set_child(self.nav)
        self.set_child(self.overlay)
        self.nav.replace([self._build_category_page()])

        if not self._rules():
            self.client.instance_info_async(self._on_instance_info)

    # Helpers

    def _rules(self):
        info = self.client.instance_info or {}
        return info.get('rules') or []

    def _on_instance_info(self, client, result, _data=None):
        try:
            client.instance_info_finish(result)
        except GLib.Error:
            return
        self.category_rows['violation'].set_visible(bool(self._rules()))

    def _make_page(self, name, title, content, suggested_label=None):
        """NavigationPage with header bar. The last step shows the
        suggested ‘Send Report’ button, the others ‘Next’."""
        header = Adw.HeaderBar()
        button = Gtk.Button(label=suggested_label or _('_Next'),
                            use_underline=True, sensitive=False)
        button.add_css_class('suggested-action')
        pages.connect_weak(button, 'clicked', self._on_next, name)
        header.pack_end(button)
        self.next_buttons[name] = button
        toolbar = Adw.ToolbarView(content=content)
        toolbar.add_top_bar(header)
        page = Adw.NavigationPage(title=title, child=toolbar, tag=name)
        self.nav_pages[name] = page
        return page, header

    def _toast(self, text):
        self.overlay.add_toast(Adw.Toast(title=text, use_markup=False))

    # Step 1: category

    def _build_category_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        name = _plain_name(self.account)
        who = Adw.ActionRow(title=GLib.markup_escape_text(name),
                            subtitle='@' + self.account.get('acct', ''),
                            title_lines=1, subtitle_lines=1)
        from .mediacache import AvatarCache
        avatar = Adw.Avatar(size=40, text=name, show_initials=True)
        AvatarCache.load(avatar, self.account)
        who.add_prefix(avatar)
        group.add(who)
        if self.collection is not None:
            collection = Adw.ActionRow(
                title=self.collection.get('name') or _('Collection'),
                subtitle=_('Collection'), title_lines=1, subtitle_lines=1,
                use_markup=False)
            collection.add_prefix(
                Gtk.Image(icon_name='view-list-symbolic'))
            group.add(collection)
        page.add(group)

        if self.collection is not None:
            subject = _('Tell us what’s going on with this collection')
        elif self.status is not None:
            subject = _('Tell us what’s going on with this post')
        else:
            subject = _('Tell us what’s going on with this account')
        choices = Adw.PreferencesGroup(
            title=subject, description=_('Choose the best match'))
        first = None
        for category in CATEGORIES:
            title, subtitle = category_info(category)
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.add_css_class('selection-mode')
            if first is None:
                first = check
            else:
                check.set_group(first)
            pages.connect_weak(check, 'toggled', self._on_category_toggled,
                               category)
            row = Adw.ActionRow(title=title, subtitle=subtitle,
                                use_markup=False, activatable_widget=check)
            row.add_prefix(check)
            if category == 'violation':
                row.set_visible(bool(self._rules()))
            self.category_checks[category] = check
            self.category_rows[category] = row
            choices.add(row)
        page.add(choices)

        nav_page, _header = self._make_page(
            'category', self.get_title(), page)
        return nav_page

    def _on_category_toggled(self, check, category):
        if check.get_active():
            self.category = category
            self.next_buttons['category'].set_sensitive(True)

    # Step 2: rules

    def _build_rules_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            title=_('Which rules are being violated?'),
            description=_('Select all that apply'))
        for rule in self._rules():
            check = Gtk.CheckButton(valign=Gtk.Align.CENTER)
            check.add_css_class('selection-mode')
            pages.connect_weak(check, 'toggled', self._on_rule_toggled)
            row = Adw.ActionRow(
                title=GLib.markup_escape_text((rule.get('text') or '').strip()),
                subtitle=GLib.markup_escape_text(
                    (rule.get('hint') or '').strip()),
                title_lines=0, activatable_widget=check)
            row.add_prefix(check)
            group.add(row)
            self.rule_checks[str(rule['id'])] = check
        page.add(group)
        nav_page, _header = self._make_page(
            'rules', _('Violated Rules'), page)
        return nav_page

    def _on_rule_toggled(self, _check):
        self.next_buttons['rules'].set_sensitive(bool(self.selected_rules()))

    def selected_rules(self):
        return [rule_id for rule_id, check in self.rule_checks.items()
                if check.get_active()]

    # Step 3: posts

    def _build_posts_page(self):
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.append(pages.section_label(
            _('Are there any other posts that back up this report?')
            if self.status else
            _('Are there any posts that back up this report?')))
        hint = Gtk.Label(label=_('Select all that apply'), xalign=0.0,
                         wrap=True)
        hint.add_css_class('dimmed')
        header.append(hint)

        # The list is ours: a strong bound method would cycle through C.
        ref = self.weak_ref()

        def make_row(status):
            dialog = ref()
            return None if dialog is None else dialog._make_post_row(status)

        self.posts_list = pages.PagedList(
            self.client, '/accounts/%s/statuses' % self.account['id'],
            make_row,
            params={'limit': 20, 'exclude_replies': 'false',
                    'exclude_reblogs': 'true'},
            empty_title=_('No Posts Found'),
            empty_icon='text-x-generic-symbolic',
            empty_description=_('You can still continue with the report.'),
            header=header)
        if self.status is not None:
            self.posts_list.prepend_row(self._make_post_row(self.status,
                                                            checked=True))
        nav_page, _header = self._make_page(
            'posts', _('Select Posts'), self.posts_list)
        self.next_buttons['posts'].set_sensitive(True)
        return nav_page

    def _make_post_row(self, status, checked=False):
        status_id = str(status['id'])
        if not checked and self.status is not None \
                and status_id == str(self.status['id']):
            return None
        row = _PostRow(status)
        row.check.set_active(checked)
        self.status_checks[status_id] = row.check
        return row

    def selected_statuses(self):
        """Selected post ids; the post the report started from first."""
        return [status_id for status_id, check in self.status_checks.items()
                if check.get_active()]

    # Step 4: comment

    def _build_comment_page(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(
            description=_('Is there anything else you think we should '
                          'know?'))
        self.comment_view = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
            left_margin=10, right_margin=10, accepts_tab=False)
        self.comment_view.update_property(
            [Gtk.AccessibleProperty.LABEL], [_('Additional Comments')])
        self.comment_buffer = self.comment_view.get_buffer()
        pages.connect_weak(self.comment_buffer, 'changed',
                           self._on_comment_changed)
        scrolled = Gtk.ScrolledWindow(
            child=self.comment_view, min_content_height=140,
            hscrollbar_policy=Gtk.PolicyType.NEVER)
        scrolled.add_css_class('card')
        self.comment_counter = Gtk.Label(
            label='0 / %d' % COMMENT_MAX, xalign=1.0)
        self.comment_counter.add_css_class('dimmed')
        self.comment_counter.add_css_class('caption')
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(scrolled)
        box.append(self.comment_counter)
        group.add(box)
        page.add(group)

        if self.domain:
            options = Adw.PreferencesGroup()
            self.forward_row = Adw.SwitchRow(
                title=_('Forward to %s') % self.domain,
                subtitle=_('Also send a copy of the report to the '
                           'moderators of that server'),
                active=True)
            options.add(self.forward_row)
            page.add(options)

        nav_page, _header = self._make_page(
            'comment', _('Additional Comments'), page,
            suggested_label=_('_Send Report'))
        self.next_buttons['comment'].set_sensitive(True)
        return nav_page

    def comment_text(self):
        start, end = self.comment_buffer.get_bounds()
        return self.comment_buffer.get_text(start, end, False)

    def _on_comment_changed(self, buffer):
        length = len(self.comment_text().strip())
        self.comment_counter.set_label('%d / %d' % (length, COMMENT_MAX))
        too_long = length > COMMENT_MAX
        if too_long:
            self.comment_counter.add_css_class('error')
        else:
            self.comment_counter.remove_css_class('error')
        self.next_buttons['comment'].set_sensitive(
            not too_long and not self._sending)

    # Navigation and submission

    def _push(self, name, builder):
        page = self.nav_pages.get(name) or builder()
        self.nav.push(page)

    def _on_next(self, _button, step):
        if step == 'category':
            if self.category == 'violation':
                self._push('rules', self._build_rules_page)
            else:
                self._push('posts', self._build_posts_page)
        elif step == 'rules':
            self._push('posts', self._build_posts_page)
        elif step == 'posts':
            self._push('comment', self._build_comment_page)
        else:
            self._submit()

    def payload(self):
        """Form for the current choices (ValueError when incomplete)."""
        return build_report_payload(
            self.account['id'], self.category,
            status_ids=self.selected_statuses(),
            rule_ids=self.selected_rules(),
            collection_ids=self.collection_ids,
            comment=self.comment_text(),
            forward=(self.forward_row.get_active()
                     if self.forward_row is not None else None))

    def _submit(self):
        if self._sending:
            return
        try:
            payload = self.payload()
        except ValueError as error:
            self._toast(str(error))
            return
        self._sending = True
        self.next_buttons['comment'].set_sensitive(False)
        self.client.request_async('POST', '/reports', form=payload,
                                  callback=self._on_sent)

    def _on_sent(self, client, result, _data=None):
        self._sending = False
        try:
            client.request_finish(result)
        except GLib.Error as error:
            self.next_buttons['comment'].set_sensitive(True)
            self._toast('%s: %s' % (
                _('Could not send report'),
                summarize_error(error)))
            return
        parent = self.parent_widget
        self.force_close()
        pages.show_toast(parent, _('Report sent'))


def present_report(parent_widget, client, account, status=None,
                   collection=None):
    """Open the report wizard for *account* (and *status* or *collection*
    when given, preselected as evidence). Returns the dialog."""
    dialog = ReportDialog(client, account, status=status,
                          collection=collection,
                          parent_widget=parent_widget)
    host = pages.window_of(parent_widget) or parent_widget
    dialog.present(host)
    return dialog
