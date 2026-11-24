# SPDX-License-Identifier: GPL-3.0-or-later
"""The quality layer on a recipe's timeline: the health banner, the
suggestions that apply with one click, the proactive alert and the A/B
experiment (its permission, its cooks, its verdict and its indicator).

A controller of ``FeedChefTimelinePage`` (the page owns it, see
``feedchefcontroller``). It reads the page's ``recipe``, ``source``,
``storage``, ``account`` and calls its ``switch_to()``. Everything that
computes (the health, the advice, the score of an experiment's cook, the
write of its state) runs on the quality worker (``feedchefquality``), and
the page only receives the answers; what happens on the main thread is a
state transition of a few dictionary entries. Nothing here starts an
experiment without the reader saying yes to the dialog that explains it.
"""

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw  # noqa: E402

from . import feedback  # noqa: E402
from .feedchef import advice, history, optimizer  # noqa: E402
from .feedchef import health as healthmod  # noqa: E402
from .feedchef.recipe import recipe_to_dict  # noqa: E402
from .feedchefcontroller import PageController, guarded  # noqa: E402
from .feedchefqualityview import QualityContext  # noqa: E402
from .feedchefquality import (ALERTED, HEALTH, feed_chef_on,  # noqa: E402
                              health_key, worker)
from .i18n import gettext as _, ngettext  # noqa: E402
from .weakconnect import connect_weak, weak_method  # noqa: E402

# The suggestions a reader said no to, by recipe identity: asked again only
# once the recipe has changed.
DECLINED = set()


def forget_session():
    """Feed Chef was turned off: what the reader declined is asked again
    after turning it on."""
    DECLINED.clear()


class ExperimentController(PageController):
    """The quality half of ``FeedChefTimelinePage``."""

    def __init__(self, page, column, words):
        """Build the two banners into *column*, wire the source's hooks and
        ask for the recipe's health. Everything is quiet until an answer
        says otherwise."""
        super().__init__(page)
        self.opt_state = None
        self._health = None
        self._health_job = None
        self.proposal_alert = None
        self._words = words
        self.health_banner = Adw.Banner(
            title=_('This recipe is getting less engagement'),
            button_label=_('Improve…'), use_markup=False)
        connect_weak(self.health_banner, 'button-clicked', self._on_improve)
        self.experiment_banner = Adw.Banner(
            button_label=_('Stop Experiment'), use_markup=False)
        connect_weak(self.experiment_banner, 'button-clicked',
                     self._on_stop_experiment)
        column.append(self.health_banner)
        column.append(self.experiment_banner)
        self.opt_state = page.storage.load_optimizer(page.recipe.id)
        self._health = HEALTH.get(health_key(page.account, page.recipe.id))
        page.source.planner = weak_method(self._plan_cook)
        page.source.variant_listener = weak_method(self._variant_landed)
        self._sync_quality_ui()
        self.request_health()

    # The banners

    def _experiment_text(self):
        """"Experiment running (3 of 10)", or None."""
        state = self.opt_state
        if optimizer.phase(state) != optimizer.RUNNING:
            return None
        # Translators: the banner of a running experiment; %(done)d of
        # %(total)d is how many of its refreshes have been tried.
        return _('Experiment running (%(done)d of %(total)d)') % {
            'done': optimizer.done(state), 'total': state['trials']}

    def _sync_quality_ui(self):
        text = self._experiment_text()
        self.experiment_banner.set_title(text or '')
        self.experiment_banner.set_revealed(text is not None)
        alert = self._health is not None and self._health.alert
        self.health_banner.set_revealed(bool(alert) and text is None)

    # The health

    @guarded
    def request_health(self):
        """Compute the health again, from the logs and the cook on screen
        (on the worker); a newer request supersedes the last."""
        page = self.owner
        if self._health_job is not None:
            self._health_job.cancel()
        recipe_id = page.recipe.id
        live = page.source.cook_audits()
        self._health_job = worker().submit(
            lambda: history.health_of(recipe_id, live),
            weak_method(self._on_health))

    @guarded
    def _on_health(self, found):
        page = self.owner
        self._health_job = None
        if found is None or not feed_chef_on():
            return
        key = health_key(page.account, page.recipe.id)
        before = HEALTH.get(key)
        HEALTH[key] = found
        self._health = found
        self._sync_quality_ui()
        # Once per session, when a recipe that was working needs attention.
        if found.state == healthmod.ATTENTION and before is not None \
                and before.state == healthmod.WORKING \
                and key not in ALERTED:
            ALERTED.add(key)
            # Translators: a toast; %s is the recipe's name.
            feedback.show_toast(
                page, _('“%s” needs attention') % page.recipe.meta.name,
                undo=weak_method(self._on_improve),
                undo_label=_('_See Suggestions'))

    def after_cook(self):
        """A page of a cook was shown."""
        self.request_health()

    # The report

    @guarded
    def quality_context(self):
        page = self.owner
        recipe, storage = page.recipe, page.storage

        def others():
            return [item for item in storage.load_recipes()
                    if item.id != recipe.id]
        mine = optimizer.identity(recipe)
        return QualityContext(
            recipe, page.source.cook_audits(), others,
            weak_method(self.apply_recommendation),
            weak_method(self.propose_experiment),
            self._experiment_text(),
            [rid for ident, rid in DECLINED if ident == mine])

    @guarded
    def _on_improve(self, *_args):
        from . import feedchefexplain
        page = self.owner
        feedchefexplain.KitchenReportDialog(
            page.source.cook_audits(), self.quality_context(),
            focus_quality=True, quality_only=True).present(page)

    # Suggestions

    @guarded
    def apply_recommendation(self, rec, anchor=None, undone=None):
        """Make a suggestion: save the recipe, cook it again and offer Undo
        (on *anchor*, the report, when it was made there). False when it
        was not made: it could not be, the recipe has it already (nothing
        is saved or cooked then), or the recipe is gone."""
        page = self.owner
        anchor = anchor or page
        try:
            done = rec.apply(page.recipe)
        except ValueError:
            feedback.show_toast(anchor, _('Could not apply this change'))
            return False
        if recipe_to_dict(done.recipe) == recipe_to_dict(page.recipe):
            return False
        if not page.switch_to(done.recipe):
            return False
        text = advice.describe(rec, self._words)
        undo = done.undo
        ref = self.weak_ref()

        def restore():
            controller = ref()
            if controller is not None:
                controller.undo_recommendation(undo)
                if undone is not None:
                    undone()

        # Translators: a toast after a suggestion was applied; %s is the
        # setting it changed, such as "Favor Fresh Posts".
        feedback.show_toast(anchor, _('Applied “%s”') % text.title,
                            undo=restore)
        return True

    @guarded
    def undo_recommendation(self, undo):
        """Put back what an applied suggestion changed, in the recipe as it
        is now (a reader's edit since stays)."""
        from .feedchef import recipeedit
        page = self.owner
        restored = recipeedit.restore(page.recipe, undo)
        if recipe_to_dict(restored) == recipe_to_dict(page.recipe):
            return
        page.switch_to(restored)

    # The experiment: permission

    @guarded
    def propose_experiment(self, recommendations):
        """Ask the reader to try the first testable change."""
        page = self.owner
        if optimizer.phase(self.opt_state) == optimizer.RUNNING:
            return
        mine = optimizer.identity(page.recipe)
        recommendations = [r for r in recommendations
                           if (mine, r.id) not in DECLINED]
        state = optimizer.propose(page.recipe, recommendations)
        if state is None:
            feedback.show_toast(page, _('There is nothing to try right '
                                        'now'))
            return
        text = advice.describe(self._recommendation_of(
            recommendations, state), self._words)
        change = '%s: %s' % (text.title, text.change) if text.change \
            else text.title
        # Translators: the question before an experiment; %s is the change
        # that will be tried, such as "Favor Fresh Posts: 6 hours → 12
        # hours"; %d is how many refreshes it takes.
        body = ngettext(
            'Babble will alternate “%(change)s” with your recipe on your '
            'next %(count)d refresh, and keep it only if it does better. '
            'You can stop at any time.',
            'Babble will alternate “%(change)s” with your recipe over your '
            'next %(count)d refreshes, and keep it only if it does '
            'better. You can stop at any time.', state['trials']) % {
            'change': change, 'count': state['trials']}
        alert = Adw.AlertDialog(heading=_('Try This Change?'), body=body)
        alert.add_response('cancel', _('_Cancel'))
        alert.add_response('start', _('_Start Test'))
        alert.set_response_appearance('start',
                                      Adw.ResponseAppearance.SUGGESTED)
        alert.set_default_response('cancel')
        alert.set_close_response('cancel')
        connect_weak(alert, 'response', self._on_proposal_answer, state,
                     mine)
        self.proposal_alert = alert
        alert.present(page)

    @staticmethod
    def _recommendation_of(recommendations, state):
        return next(rec for rec in recommendations
                    if rec.id == state['change']['id'])

    def _on_proposal_answer(self, _alert, response, state, mine):
        self.proposal_alert = None
        if response != 'start':
            DECLINED.add((mine, state['change']['id']))
            return
        self._fresh_state()
        self._set_experiment(optimizer.accept(state))

    # The experiment: cooks

    @guarded
    def _fresh_state(self):
        """The experiment as the sidecar has it now: another window on the
        same recipe may have moved it. A small file, read before each
        transition so that no window overwrites another's with its own
        older copy."""
        page = self.owner
        self.opt_state = page.storage.load_optimizer(page.recipe.id)
        return self.opt_state

    @guarded
    def _set_experiment(self, state):
        """Keep *state*: the page's, and the sidecar's (written at once, a
        file of a few hundred bytes; none is left for a recipe that was
        deleted)."""
        page = self.owner
        self.opt_state = state
        page.storage.save_optimizer(page.recipe.id, state,
                                    require_recipe=True)
        self._sync_quality_ui()

    @guarded
    def _plan_cook(self):
        """What a refresh cooks (called by the source on the main thread; a
        few dictionary lookups, and a hash when an experiment runs)."""
        page = self.owner
        state = self._fresh_state()
        self._sync_quality_ui()
        if optimizer.phase(state) != optimizer.RUNNING:
            return None
        new, plan = optimizer.next_cook(state, page.recipe)
        if new is not state:
            self._set_experiment(new)
            feedback.show_toast(
                page, _('The experiment ended because the recipe was '
                        'changed'))
        return plan if plan.tag else None

    def _variant_landed(self, tag, record):
        """A page of an experiment's cook was shown: score it on the worker."""
        if record is None:
            return
        ref = self.weak_ref()

        def deliver(score):
            controller = ref()
            if controller is not None:
                controller._on_scored(tag, score)

        worker().submit(lambda: optimizer.score_of([record], tag), deliver)

    @guarded
    def _on_scored(self, tag, score):
        state = self._fresh_state()
        if score is None or optimizer.phase(state) != optimizer.RUNNING:
            return
        new = optimizer.record_result(state, tag, score)
        if new is state:
            return
        self._set_experiment(new)
        if new['phase'] in (optimizer.KEPT, optimizer.REVERTED):
            self._experiment_finished(new)

    @guarded
    def _experiment_finished(self, state):
        page = self.owner
        if state['phase'] == optimizer.REVERTED:
            feedback.show_toast(
                page, _('Experiment finished: the change did not help, so '
                        'your recipe stays as it was'))
            return
        done = optimizer.result_recipe(state, page.recipe)
        if done is None or not page.switch_to(done):
            feedback.show_toast(
                page, _('Could not keep the change'))
            return
        ref = self.weak_ref()

        def restore():
            controller = ref()
            if controller is not None:
                controller.stop_experiment()

        feedback.show_toast(
            page, _('Experiment finished: change kept'), undo=restore)

    # The experiment: stopping

    def _on_stop_experiment(self, *_args):
        self.stop_experiment()

    @guarded
    def stop_experiment(self):
        """Switch the experiment off and put the recipe back as it was
        (the change is undone only when it was kept; the recipe itself is
        never touched while the experiment only alternates)."""
        page = self.owner
        state = self._fresh_state()
        if state is None:
            return
        restored = optimizer.restore(state, page.recipe)
        self._set_experiment(optimizer.turn_off(state))
        if restored is None:
            # Edited since: nothing of the reader's is overwritten.
            text = _('Experiment stopped; your later edits were kept')
        elif recipe_to_dict(restored) != recipe_to_dict(page.recipe):
            text = _('Experiment stopped; change undone') \
                if page.switch_to(restored) else None
        else:
            text = _('Experiment stopped; recipe unchanged')
        if text:
            feedback.show_toast(page, text)

    def close(self):
        """The page is going: nothing it started may answer it."""
        if self._health_job is not None:
            self._health_job.cancel()
