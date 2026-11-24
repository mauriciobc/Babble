# SPDX-License-Identifier: GPL-3.0-or-later
"""New Recipe: what to start from. A template is a recipe with the settings
its use case needs (``feedchef.templates``); the chooser makes it and hands
it to the builder, where everything can still be changed."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from .feedback import ToastDialog  # noqa: E402
from .feedchef import templates  # noqa: E402
from .feedchef.icons import icon_name  # noqa: E402
from .feedchefexplain import WORDS  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak  # noqa: E402

# The recipe icon each template starts with (Blank keeps the default).
TEMPLATE_ICONS = {'recent_news': 'NEWS', 'viral': 'STAR', 'topic': 'TAG',
                  'safe_feed': 'SHIELD'}


class NewRecipeDialog(ToastDialog):
    """Lists the templates; choosing one closes the dialog and emits
    ``chosen``, with the new recipe in ``recipe``."""

    __gtype_name__ = 'BabbleFeedChefNewRecipeDialog'
    __gsignals__ = {'chosen': (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self):
        super().__init__(_('New Recipe'), width=420, height=560)
        self.recipe = None
        group = Adw.PreferencesGroup(
            title=_('Start From'),
            description=_('You can change everything in the builder'))
        actions = Gio.SimpleActionGroup()
        choose = Gio.SimpleAction.new('choose', GLib.VariantType.new('s'))
        connect_weak(choose, 'activate', self._on_choose)
        actions.add_action(choose)
        self.insert_action_group('template', actions)
        self.rows = {}
        for template_id in templates.TEMPLATE_IDS:
            name, line = templates.describe(template_id, WORDS)
            row = Adw.ActionRow(title=name, subtitle=line, use_markup=False,
                                activatable=True)
            row.set_action_name('template.choose')
            row.set_action_target_value(
                GLib.Variant.new_string(template_id))
            enum = TEMPLATE_ICONS.get(template_id)
            row.add_prefix(Gtk.Image(
                icon_name=icon_name(enum) if enum
                else 'document-new-symbolic',
                accessible_role=Gtk.AccessibleRole.PRESENTATION))
            row.add_suffix(Gtk.Image.new_from_icon_name('go-next-symbolic'))
            group.add(row)
            self.rows[template_id] = row
        self.page.add(group)

    def _on_choose(self, _action, parameter):
        template_id = parameter.get_string()
        recipe = templates.new_recipe(
            template_id, words=WORDS,
            name=_('New Recipe') if template_id == 'blank' else None)
        enum = TEMPLATE_ICONS.get(template_id)
        if enum:
            recipe.meta.icon = enum
        self.recipe = recipe
        self.force_close()
        self.emit('chosen')
