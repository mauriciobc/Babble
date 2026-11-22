# SPDX-License-Identifier: GPL-3.0-or-later
"""Feed Chef recipe builder — controls match mashdon FeedChefBuilderFragment."""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk  # noqa: E402

from . import feedback  # noqa: E402
from .api import lists as api_lists  # noqa: E402
from .feedchef.recipe import Modifier, default_recipe  # noqa: E402
from .feedchef.sharecodec import recipe_share_uri  # noqa: E402
from .feedchef.storage import FeedRecipeStorage  # noqa: E402
from .i18n import gettext as _  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

# Java TimelineDefinition.Icon values with hidden=false, plus a GTK name.
_RECIPE_ICONS = (
    ('HEART', 'emoji-body-symbolic', _('Heart')),
    ('STAR', 'starred-symbolic', _('Star')),
    ('PEOPLE', 'system-users-symbolic', _('People')),
    ('CITY', 'mark-location-symbolic', _('City')),
    ('IMAGE', 'image-x-generic-symbolic', _('Image')),
    ('NEWS', 'newspaper-symbolic', _('News')),
    ('FEED', 'application-rss+xml-symbolic', _('Feed')),
    ('COLOR_PALETTE', 'preferences-color-symbolic', _('Palette')),
    ('CAT', 'emoji-nature-symbolic', _('Cat')),
    ('DOG', 'emoji-nature-symbolic', _('Dog')),
    ('RABBIT', 'emoji-nature-symbolic', _('Rabbit')),
    ('TURTLE', 'emoji-nature-symbolic', _('Turtle')),
    ('ACADEMIC_CAP', 'accessories-dictionary-symbolic', _('Academic cap')),
    ('BOT', 'computer-symbolic', _('Bot')),
    ('IMPORTANT', 'dialog-warning-symbolic', _('Important')),
    ('PIN', 'view-pin-symbolic', _('Pin')),
    ('SHIELD', 'security-medium-symbolic', _('Shield')),
    ('CHAT', 'user-available-symbolic', _('Chat')),
    ('TAG', 'folder-tag-symbolic', _('Tag')),
    ('TRAIN', 'emoji-travel-symbolic', _('Train')),
    ('BICYCLE', 'emoji-travel-symbolic', _('Bicycle')),
    ('MAP', 'mark-location-symbolic', _('Map')),
    ('BACKPACK', 'packaged-symbolic', _('Backpack')),
    ('BRIEFCASE', 'x-office-address-book-symbolic', _('Briefcase')),
    ('BOOK', 'accessories-dictionary-symbolic', _('Book')),
    ('LANGUAGE', 'preferences-desktop-locale-symbolic', _('Language')),
    ('WEATHER', 'weather-showers-symbolic', _('Weather')),
    ('APERTURE', 'camera-photo-symbolic', _('Aperture')),
    ('MUSIC', 'audio-x-generic-symbolic', _('Music')),
    ('LOCATION', 'mark-location-symbolic', _('Location')),
    ('GLOBE', 'network-workgroup-symbolic', _('Globe')),
    ('MEGAPHONE', 'audio-speakers-symbolic', _('Megaphone')),
    ('MICROPHONE', 'audio-input-microphone-symbolic', _('Microphone')),
    ('MICROSCOPE', 'emblem-science-symbolic', _('Microscope')),
    ('STETHOSCOPE', 'emoji-body-symbolic', _('Stethoscope')),
    ('KEYBOARD', 'input-keyboard-symbolic', _('Keyboard')),
    ('COFFEE', 'emoji-food-symbolic', _('Coffee')),
    ('CLAPPER_BOARD', 'camera-video-symbolic', _('Clapper board')),
    ('LAUGH', 'face-smile-symbolic', _('Laugh')),
    ('BALLOON', 'emoji-objects-symbolic', _('Balloon')),
    ('PI', 'accessories-calculator-symbolic', _('Pi')),
    ('MATH_FORMULA', 'accessories-calculator-symbolic', _('Math')),
    ('GAMES', 'applications-games-symbolic', _('Games')),
    ('CODE', 'utilities-terminal-symbolic', _('Code')),
    ('BUG', 'emoji-nature-symbolic', _('Bug')),
    ('LIGHT_BULB', 'dialog-information-symbolic', _('Light bulb')),
    ('FIRE', 'weather-clear-symbolic', _('Fire')),
    ('LEAVES', 'emoji-nature-symbolic', _('Leaves')),
    ('SPORT', 'emoji-people-symbolic', _('Sport')),
    ('HEALTH', 'emoji-body-symbolic', _('Health')),
    ('PIZZA', 'emoji-food-symbolic', _('Pizza')),
    ('GAVEL', 'preferences-system-symbolic', _('Gavel')),
    ('GAUGE', 'utilities-system-monitor-symbolic', _('Gauge')),
    ('HEADPHONES', 'audio-headphones-symbolic', _('Headphones')),
    ('HUMAN', 'preferences-desktop-accessibility-symbolic', _('Human')),
    ('BEAKER', 'emblem-science-symbolic', _('Beaker')),
    ('BED', 'emoji-objects-symbolic', _('Bed')),
    ('RECYCLE_BIN', 'user-trash-symbolic', _('Recycle bin')),
    ('VERIFIED', 'emblem-ok-symbolic', _('Verified')),
    ('DOCTOR', 'emoji-people-symbolic', _('Doctor')),
    ('DIAMOND', 'emoji-objects-symbolic', _('Diamond')),
    ('UMBRELLA', 'weather-showers-symbolic', _('Umbrella')),
    ('WATER', 'weather-showers-symbolic', _('Water')),
    ('SUN', 'weather-clear-symbolic', _('Sun')),
    ('SUNSET', 'weather-clear-symbolic', _('Sunset')),
    ('CLOUD', 'weather-overcast-symbolic', _('Cloud')),
    ('THUNDERSTORM', 'weather-storm-symbolic', _('Thunderstorm')),
    ('RAIN', 'weather-showers-symbolic', _('Rain')),
    ('SNOWFLAKE', 'weather-snow-symbolic', _('Snowflake')),
    ('GNOME', 'start-here-symbolic', _('GNOME')),
)
_ICON_BY_NAME = {name: (gtk, label) for name, gtk, label in _RECIPE_ICONS}
_SCOPES = ('home', 'local', 'federated', 'list')
_CLUSTER_STRATEGIES = ('best_score', 'most_recent', 'centroid')


def _account_key(account):
    instance = (account.get('instance') or '').rstrip('/')
    return '%s#%s' % (instance, account.get('id') or '')


def _mod(recipe, typ):
    for item in recipe.kitchen.modifiers:
        if item.type == typ:
            return item
    return None


def _upsert(recipe, typ, **fields):
    item = _mod(recipe, typ)
    if item is None:
        item = Modifier(type=typ)
        recipe.kitchen.modifiers.append(item)
    for key, value in fields.items():
        setattr(item, key, value)
    return item


def _remove(recipe, typ):
    recipe.kitchen.modifiers = [
        item for item in recipe.kitchen.modifiers if item.type != typ]


def _heat_label(progress):
    if progress < 25:
        return _('Cool')
    if progress < 50:
        return _('Warm')
    if progress < 75:
        return _('Hot')
    return _('Fire')


def _icon_gtk(name):
    entry = _ICON_BY_NAME.get((name or '').upper())
    if entry is None and (name or '').upper() == 'RESTAURANT':
        entry = _ICON_BY_NAME.get('PIZZA')
    if entry is None:
        return 'emoji-food-symbolic'
    return entry[0]


class FeedChefBuilderDialog(Adw.PreferencesDialog):
    """Builder UI fields match mashdon ``FeedChefBuilderFragment``."""

    __gtype_name__ = 'BabbleFeedChefBuilderDialog'

    def __init__(self, client, account, recipe=None):
        super().__init__(title=_('Feed Chef Recipe'))
        self.client = client
        self.account = account
        self.recipe = recipe or default_recipe()
        self.storage = FeedRecipeStorage(_account_key(account))
        self._lists = []  # [{'id', 'title'}, …]
        self._build_ui()
        self._load_lists()

    def _build_ui(self):
        recipe = self.recipe
        general = Adw.PreferencesPage(
            title=_('Recipe'), icon_name='document-edit-symbolic')
        self.add(general)
        meta = Adw.PreferencesGroup(title=_('Recipe'))
        self.name_row = Adw.EntryRow(title=_('Name'))
        self.name_row.set_text(recipe.meta.name)
        meta.add(self.name_row)
        self.icon_row = Adw.ActionRow(
            title=_('Icon'),
            subtitle=self._icon_subtitle())
        pick = Gtk.Button(label=_('Choose'), valign=Gtk.Align.CENTER)
        connect_weak(pick, 'clicked', self._pick_icon)
        self.icon_row.add_suffix(pick)
        self.icon_image = Gtk.Image(icon_name=_icon_gtk(recipe.meta.icon))
        self.icon_row.add_prefix(self.icon_image)
        meta.add(self.icon_row)
        general.add(meta)

        pantry_page = Adw.PreferencesPage(
            title=_('Pantry'), icon_name='folder-symbolic')
        self.add(pantry_page)
        pantry = Adw.PreferencesGroup(
            title=_('Pantry'),
            description=_('Where posts come from'))
        self.scope = Adw.ComboRow(title=_('Scope'))
        self.scope.set_model(Gtk.StringList.new(list(_SCOPES)))
        try:
            self.scope.set_selected(_SCOPES.index(recipe.pantry.scope))
        except ValueError:
            self.scope.set_selected(0)
        connect_weak(self.scope, 'notify::selected', self._scope_changed)
        pantry.add(self.scope)
        self.list_row = Adw.ComboRow(title=_('List'))
        self._list_model = Gtk.StringList.new([_('None')])
        self.list_row.set_model(self._list_model)
        self.list_row.set_selected(0)
        pantry.add(self.list_row)
        self.include = Adw.EntryRow(title=_('Include hashtags'))
        self.include.set_text(' '.join(recipe.pantry.hashtags_include))
        pantry.add(self.include)
        self.exclude = Adw.EntryRow(title=_('Exclude hashtags'))
        self.exclude.set_text(' '.join(recipe.pantry.hashtags_exclude))
        pantry.add(self.exclude)
        pantry_page.add(pantry)

        reach_group = Adw.PreferencesGroup(title=_('Network reach'))
        self.reach_switch = Adw.SwitchRow(
            title=_('Network reach'),
            subtitle=_('Degree-1 following only; Unlimited turns the filter off'))
        self.reach_switch.set_active(recipe.pantry.network_reach.enabled)
        connect_weak(self.reach_switch, 'notify::active', self._reach_toggled)
        reach_group.add(self.reach_switch)
        # Java slider 0→1° … 4→5°, 5→null (“Unlimited”).
        degrees = recipe.pantry.network_reach.max_degrees
        if degrees is None:
            reach_progress = 5
        else:
            reach_progress = max(0, min(5, int(degrees) - 1))
        self.reach_degrees = Adw.SpinRow(
            title=_('Max degrees'),
            subtitle=_('6 means Unlimited (filter off)'),
            adjustment=Gtk.Adjustment(
                value=float(reach_progress + 1),
                lower=1, upper=6, step_increment=1),
            digits=0)
        connect_weak(self.reach_degrees, 'notify::value',
                     self._update_reach_subtitle)
        reach_group.add(self.reach_degrees)
        self.include_unknown = Adw.SwitchRow(
            title=_('Include unknown'),
            subtitle=_('Keep authors not yet in the following cache'))
        self.include_unknown.set_active(
            recipe.pantry.network_reach.include_unknown)
        reach_group.add(self.include_unknown)
        pantry_page.add(reach_group)
        self._reach_toggled()
        self._update_reach_subtitle()
        self._scope_changed()

        diet_page = Adw.PreferencesPage(
            title=_('Diet'), icon_name='dialog-warning-symbolic')
        self.add(diet_page)
        diet = Adw.PreferencesGroup(title=_('Diet'))
        self.blocklist = Adw.EntryRow(title=_('Keyword blocklist'))
        self.blocklist.set_text(' '.join(recipe.diet.keyword_blocklist))
        diet.add(self.blocklist)
        self.alt = Adw.SwitchRow(title=_('Must have alt text'))
        self.alt.set_active(recipe.diet.hard_filters.must_have_alt_text)
        diet.add(self.alt)
        self.sensitive = Adw.SwitchRow(title=_('No sensitive media'))
        self.sensitive.set_active(recipe.diet.hard_filters.no_sensitive_media)
        diet.add(self.sensitive)
        self.positive_only = Adw.SwitchRow(
            title=_('Positive only'),
            subtitle=_('Drop posts with a negative VADER / LeIA score'))
        self.positive_only.set_active(recipe.diet.hard_filters.positive_only)
        diet.add(self.positive_only)
        self.min_length = Adw.SpinRow(
            title=_('Minimum content length'),
            adjustment=Gtk.Adjustment(
                value=float(recipe.diet.hard_filters.min_content_length),
                lower=0, upper=500, step_increment=1),
            digits=0)
        diet.add(self.min_length)
        diet_page.add(diet)

        kitchen_page = Adw.PreferencesPage(
            title=_('Kitchen'), icon_name='preferences-system-symbolic')
        self.add(kitchen_page)
        kitchen = Adw.PreferencesGroup(
            title=_('Kitchen'),
            description=_('Similarity clustering stays inactive without NLP'))
        self.base_sort = Adw.ComboRow(title=_('Base sort'))
        self.base_sort.set_model(Gtk.StringList.new(
            [_('Score'), _('Chronological')]))
        self.base_sort.set_selected(
            1 if recipe.kitchen.base_sort == 'chronological' else 0)
        kitchen.add(self.base_sort)

        heat = _mod(recipe, 'heat')
        heat_value = float(heat.percentile_threshold) if heat and \
            heat.percentile_threshold is not None else 0.0
        self.heat = Adw.SpinRow(
            title=_('Heat'),
            subtitle=_heat_label(int(heat_value)),
            adjustment=Gtk.Adjustment(
                value=heat_value, lower=0, upper=100, step_increment=1),
            digits=0)
        connect_weak(self.heat, 'notify::value', self._heat_changed)
        kitchen.add(self.heat)

        self.freshness = Adw.SwitchRow(
            title=_('Freshness'),
            subtitle=_('Decay score over 6 hours'))
        self.freshness.set_active(_mod(recipe, 'freshness') is not None)
        kitchen.add(self.freshness)
        kitchen_page.add(kitchen)

        sim = _mod(recipe, 'similarity_cluster')
        sim_group = Adw.PreferencesGroup(
            title=_('Similarity clustering'),
            description=_('Inactive without NLP — saved for mashdon parity'))
        self.similarity = Adw.SwitchRow(title=_('Similarity clustering'))
        self.similarity.set_active(sim is not None)
        connect_weak(self.similarity, 'notify::active', self._sim_toggled)
        sim_group.add(self.similarity)
        thresh = 60.0 if sim is None or sim.similarity_threshold is None \
            else float(sim.similarity_threshold) * 100.0
        self.sim_threshold = Adw.SpinRow(
            title=_('Similarity threshold'),
            adjustment=Gtk.Adjustment(
                value=thresh, lower=0, upper=100, step_increment=1),
            digits=0)
        sim_group.add(self.sim_threshold)
        max_cluster = 3.0 if sim is None or sim.max_per_cluster is None \
            else float(sim.max_per_cluster)
        self.sim_max = Adw.SpinRow(
            title=_('Max per cluster'),
            adjustment=Gtk.Adjustment(
                value=max_cluster, lower=0, upper=10, step_increment=1),
            digits=0)
        sim_group.add(self.sim_max)
        self.sim_strategy = Adw.ComboRow(title=_('Cluster strategy'))
        self.sim_strategy.set_model(Gtk.StringList.new([
            _('Best score'), _('Most recent'), _('Centroid')]))
        strategy = (sim.cluster_strategy if sim else None) or 'best_score'
        try:
            self.sim_strategy.set_selected(
                _CLUSTER_STRATEGIES.index(strategy))
        except ValueError:
            self.sim_strategy.set_selected(0)
        sim_group.add(self.sim_strategy)
        kitchen_page.add(sim_group)
        self._sim_toggled()

        div = _mod(recipe, 'diversity')
        div_group = Adw.PreferencesGroup(title=_('Diversity'))
        self.diversity = Adw.SwitchRow(title=_('Diversity'))
        self.diversity.set_active(div is not None)
        connect_weak(self.diversity, 'notify::active', self._div_toggled)
        div_group.add(self.diversity)
        self.div_author = Adw.SwitchRow(title=_('Author dimension'))
        dims = (div.diversity_dimensions if div else None) or ['author']
        self.div_author.set_active('author' in dims)
        div_group.add(self.div_author)
        # Java layout default progress is 2; default_recipe() ships 3.
        if div is not None and div.max_per_cluster is not None:
            div_max = float(div.max_per_cluster)
        else:
            div_max = 2.0
        self.div_max = Adw.SpinRow(
            title=_('Max per author'),
            adjustment=Gtk.Adjustment(
                value=div_max, lower=0, upper=5, step_increment=1),
            digits=0)
        div_group.add(self.div_max)
        kitchen_page.add(div_group)
        self._div_toggled()

        sent = _mod(recipe, 'sentiment_boost')
        sent_group = Adw.PreferencesGroup(title=_('Sentiment boost'))
        self.sentiment = Adw.SwitchRow(
            title=_('Sentiment boost'),
            subtitle=_('Boost positive VADER / LeIA compound scores'))
        self.sentiment.set_active(sent is not None)
        connect_weak(self.sentiment, 'notify::active', self._sent_toggled)
        sent_group.add(self.sentiment)
        boost = 20.0 if sent is None or sent.sentiment_boost is None \
            else float(sent.sentiment_boost) * 100.0
        self.sentiment_value = Adw.SpinRow(
            title=_('Boost amount'),
            subtitle=_('Percent of the compound score added to rank'),
            adjustment=Gtk.Adjustment(
                value=boost, lower=0, upper=100, step_increment=1),
            digits=0)
        sent_group.add(self.sentiment_value)
        kitchen_page.add(sent_group)
        self._sent_toggled()

        trend = _mod(recipe, 'trending')
        trend_group = Adw.PreferencesGroup(title=_('Trending topics'))
        self.trending = Adw.SwitchRow(title=_('Trending topics'))
        self.trending.set_active(trend is not None)
        connect_weak(self.trending, 'notify::active', self._trend_toggled)
        trend_group.add(self.trending)
        growth = 30.0 if trend is None or trend.growth_threshold is None \
            else float(trend.growth_threshold) * 100.0
        self.trend_growth = Adw.SpinRow(
            title=_('Growth threshold'),
            subtitle=_('Percent; 30 matches the Java default'),
            adjustment=Gtk.Adjustment(
                value=growth, lower=0, upper=100, step_increment=1),
            digits=0)
        trend_group.add(self.trend_growth)
        kitchen_page.add(trend_group)
        self._trend_toggled()

        inert = recipe.inert_modifiers()
        if inert:
            warn = Adw.PreferencesGroup()
            warn.add(Adw.ActionRow(
                title=_('Inactive modifiers'),
                subtitle=', '.join(inert)))
            kitchen_page.add(warn)

        actions_page = Adw.PreferencesPage(
            title=_('Save'), icon_name='document-save-symbolic')
        self.add(actions_page)
        actions = Adw.PreferencesGroup()
        save = Adw.ButtonRow(title=_('Save'))
        connect_weak(save, 'activated', self._save)
        actions.add(save)
        share = Adw.ButtonRow(title=_('Copy share link'))
        connect_weak(share, 'activated', self._share)
        actions.add(share)
        actions_page.add(actions)

    def _icon_subtitle(self):
        name = (self.recipe.meta.icon or 'PIZZA').upper()
        if name == 'RESTAURANT':
            name = 'PIZZA'
        entry = _ICON_BY_NAME.get(name)
        return entry[1] if entry else name

    def _pick_icon(self, *_args):
        dialog = Adw.AlertDialog(
            heading=_('Choose an icon'),
            body=_('Icons use the same names as mashdon recipes'))
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('pick', _('Use selected'))
        dialog.set_default_response('pick')
        dialog.set_response_appearance(
            'pick', Adw.ResponseAppearance.SUGGESTED)
        scrolled = Gtk.ScrolledWindow(
            min_content_height=280, max_content_height=360,
            min_content_width=360, hexpand=True, vexpand=True)
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            max_children_per_line=6, min_children_per_line=4,
            homogeneous=True)
        current = (self.recipe.meta.icon or 'PIZZA').upper()
        if current == 'RESTAURANT':
            current = 'PIZZA'
        for name, gtk_name, label in _RECIPE_ICONS:
            child = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            child.set_margin_top(4)
            child.set_margin_bottom(4)
            child.append(Gtk.Image(icon_name=gtk_name, pixel_size=24))
            child.append(Gtk.Label(label=label, wrap=True))
            flow.append(child)
            if name == current:
                flow.select_child(flow.get_last_child())
        scrolled.set_child(flow)
        dialog.set_extra_child(scrolled)

        def on_response(_dialog, response):
            if response != 'pick':
                return
            selected = flow.get_selected_children()
            if not selected:
                return
            index = selected[0].get_index()
            name = _RECIPE_ICONS[index][0]
            self.recipe.meta.icon = name
            self.icon_image.set_from_icon_name(_icon_gtk(name))
            self.icon_row.set_subtitle(self._icon_subtitle())

        connect_weak(dialog, 'response', on_response)
        dialog.present(self)

    def _load_lists(self):
        api_lists.index_async(
            self.client, callback=weak_method(self._on_lists_loaded))

    def _on_lists_loaded(self, result):
        try:
            data = api_lists.index_finish(self.client, result)
        except Exception as error:
            feedback.report_error(self, _('Feed Chef'), error)
            return
        if not isinstance(data, list):
            data = []
        self._lists = [
            {'id': str(item.get('id') or ''),
             'title': str(item.get('title') or item.get('id') or '')}
            for item in data if item.get('id') is not None]
        titles = [_('None')] + [item['title'] for item in self._lists]
        self._list_model.splice(0, self._list_model.get_n_items(), titles)
        wanted = self.recipe.pantry.list_id
        selected = 0
        if wanted:
            for index, item in enumerate(self._lists):
                if item['id'] == str(wanted):
                    selected = index + 1
                    break
        self.list_row.set_selected(selected)

    def _scope_changed(self, *_args):
        is_list = _SCOPES[self.scope.get_selected()] == 'list'
        self.list_row.set_sensitive(is_list)
        self.list_row.set_visible(is_list)

    def _reach_toggled(self, *_args):
        active = self.reach_switch.get_active()
        self.reach_degrees.set_sensitive(active)
        self.include_unknown.set_sensitive(active)

    def _update_reach_subtitle(self, *_args):
        progress = int(self.reach_degrees.get_value()) - 1
        if progress >= 5:
            self.reach_degrees.set_subtitle(_('Unlimited (filter off)'))
        else:
            # Translators: %d is the following-graph degree
            self.reach_degrees.set_subtitle(
                _('Up to %d degree(s)') % (progress + 1))

    def _heat_changed(self, *_args):
        self.heat.set_subtitle(_heat_label(int(self.heat.get_value())))

    def _sim_toggled(self, *_args):
        active = self.similarity.get_active()
        self.sim_threshold.set_sensitive(active)
        self.sim_max.set_sensitive(active)
        self.sim_strategy.set_sensitive(active)

    def _div_toggled(self, *_args):
        active = self.diversity.get_active()
        self.div_author.set_sensitive(active)
        self.div_max.set_sensitive(active)

    def _sent_toggled(self, *_args):
        self.sentiment_value.set_sensitive(self.sentiment.get_active())

    def _trend_toggled(self, *_args):
        self.trend_growth.set_sensitive(self.trending.get_active())

    def _collect(self):
        recipe = self.recipe
        recipe.meta.name = self.name_row.get_text().strip() or _(
            'Untitled Recipe')
        if not (recipe.meta.icon or '').strip():
            recipe.meta.icon = 'PIZZA'
        recipe.pantry.scope = _SCOPES[self.scope.get_selected()]
        selected = self.list_row.get_selected()
        if recipe.pantry.scope == 'list' and selected > 0 \
                and selected - 1 < len(self._lists):
            recipe.pantry.list_id = self._lists[selected - 1]['id']
        else:
            recipe.pantry.list_id = None
        recipe.pantry.hashtags_include = [
            part.lstrip('#').lower()
            for part in self.include.get_text().split() if part.strip()]
        recipe.pantry.hashtags_exclude = [
            part.lstrip('#').lower()
            for part in self.exclude.get_text().split() if part.strip()]
        recipe.pantry.network_reach.enabled = self.reach_switch.get_active()
        progress = int(self.reach_degrees.get_value()) - 1
        recipe.pantry.network_reach.max_degrees = (
            None if progress >= 5 else progress + 1)
        recipe.pantry.network_reach.include_unknown = (
            self.include_unknown.get_active())
        recipe.diet.keyword_blocklist = [
            part.lower() for part in self.blocklist.get_text().split()
            if part.strip()]
        hard = recipe.diet.hard_filters
        hard.must_have_alt_text = self.alt.get_active()
        hard.no_sensitive_media = self.sensitive.get_active()
        hard.positive_only = self.positive_only.get_active()
        hard.min_content_length = int(self.min_length.get_value())
        recipe.kitchen.base_sort = (
            'chronological' if self.base_sort.get_selected() == 1 else 'score')

        heat_value = int(self.heat.get_value())
        # Java keeps heat once the slider moves, including 0. We treat 0 as
        # “off” so a new recipe is not saved with a zeroed heat filter.
        if heat_value > 0:
            _upsert(recipe, 'heat', percentile_threshold=heat_value)
        else:
            _remove(recipe, 'heat')

        if self.freshness.get_active():
            _upsert(recipe, 'freshness', decay_minutes=360)
        else:
            _remove(recipe, 'freshness')

        if self.similarity.get_active():
            strategy = _CLUSTER_STRATEGIES[self.sim_strategy.get_selected()]
            _upsert(
                recipe, 'similarity_cluster',
                similarity_threshold=self.sim_threshold.get_value() / 100.0,
                max_per_cluster=int(self.sim_max.get_value()),
                cluster_strategy=strategy)
        else:
            _remove(recipe, 'similarity_cluster')

        if self.diversity.get_active():
            dims = []
            if self.div_author.get_active():
                dims.append('author')
            _upsert(
                recipe, 'diversity',
                max_per_cluster=int(self.div_max.get_value()),
                diversity_dimensions=dims)
        else:
            _remove(recipe, 'diversity')

        if self.sentiment.get_active():
            _upsert(
                recipe, 'sentiment_boost',
                sentiment_boost=self.sentiment_value.get_value() / 100.0)
        else:
            _remove(recipe, 'sentiment_boost')

        if self.trending.get_active():
            growth = self.trend_growth.get_value() / 100.0
            if growth <= 0:
                growth = 0.3
            _upsert(
                recipe, 'trending',
                growth_threshold=growth,
                time_window_hours=None)
        else:
            _remove(recipe, 'trending')
        return recipe

    def _save(self, *_args):
        self.storage.save_recipe(self._collect())
        feedback.show_toast(self, _('Recipe saved'))
        self.close()

    def _share(self, *_args):
        recipe = self._collect()
        uri = recipe_share_uri(recipe)
        display = self.get_display()
        if display is not None:
            display.get_clipboard().set(uri)
        feedback.show_toast(self, _('Share link copied'))
