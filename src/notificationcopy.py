# SPDX-License-Identifier: GPL-3.0-or-later

"""Notification title and type-label strings.

The in-app notifications list, desktop notifications and the preferences
dialog all describe the same Mastodon types; this module is where those
strings live so translators meet each wording once.
"""

from .i18n import gettext as _


def _desktop_pair(one, many=None):
    """``(one actor, several actors)`` for :func:`desktopnotify.notification_title`."""
    return (one, many if many is not None else one)


# In-app list titles. ``%s`` is filled by ``notifications._subject``.
LIST_TITLES = {
    'favourite': _('%s favorited your post'),
    'reblog': _('%s boosted your post'),
    'follow': _('%s followed you'),
    'follow_request': _('%s requested to follow you'),
    'mention': _('%s mentioned you'),
    'poll': _('%s’s poll has ended'),
    'update': _('%s edited a post you interacted with'),
    'status': _('%s posted'),
    'quote': _('%s quoted your post'),
    'quoted_update': _('%s edited a post you quoted'),
    'admin.sign_up': _('%s signed up'),
    'added_to_collection': _('%s added you to a collection'),
    'collection_update': _('%s updated a collection you are in'),
}
LIST_TITLE_DEFAULT = _('%s interacted with you')

# Desktop notification titles: ``%s`` is ``desktopnotify.actor_list(names)``.
DESKTOP_TITLES = {
    'mention': _desktop_pair(_('%s mentioned you')),
    'reblog': _desktop_pair(_('%s boosted your post')),
    'favourite': _desktop_pair(_('%s favorited your post')),
    'follow': _desktop_pair(_('%s followed you')),
    'follow_request': _desktop_pair(_('%s requested to follow you')),
    'poll': _desktop_pair(_('%s’s poll has ended'),
                         _('%s’s polls have ended')),
    'status': _desktop_pair(_('%s posted')),
    'update': _desktop_pair(_('%s edited a post you interacted with')),
    'quote': _desktop_pair(_('%s quoted your post')),
    'quoted_update': _desktop_pair(_('%s edited a post you quoted')),
    'admin.sign_up': _desktop_pair(_('%s signed up')),
    'admin.report': _desktop_pair(_('%s reported a post'),
                                  _('%s reported posts')),
    'added_to_collection': _desktop_pair(
        _('%s added you to a collection')),
    'collection_update': _desktop_pair(
        _('%s updated a collection you are in')),
}
DESKTOP_TITLE_DEFAULT = _desktop_pair(_('%s interacted with you'))

# Shared type labels (filter menu, settings dialog, desktop prefs).
LABEL_MENTIONS = _('Mentions')
LABEL_NEW_POSTS = _('New Posts')
LABEL_BOOSTS = _('Boosts')
LABEL_FAVORITES = _('Favorites')
LABEL_FOLLOWS = _('Follows')
LABEL_FOLLOW_REQUESTS = _('Follow Requests')
LABEL_POLLS = _('Polls')
LABEL_POST_EDITS = _('Post Edits')
LABEL_QUOTES = _('Quotes')
LABEL_QUOTE_EDITS = _('Quote Edits')
LABEL_SIGN_UPS = _('Sign-Ups')
LABEL_REPORTS = _('Reports')
LABEL_SEVERED_RELATIONSHIPS = _('Severed Relationships')
LABEL_MODERATION_WARNINGS = _('Moderation Warnings')
LABEL_ANNUAL_REPORTS = _('Annual Reports')
LABEL_ADDED_TO_COLLECTIONS = _('Added to Collections')
LABEL_COLLECTION_UPDATES = _('Collection Updates')

# Desktop notification preferences use different headings where the
# context is "what to announce", not "what to show in the list".
LABEL_NEW_FOLLOWERS = _('New Followers')
LABEL_POLL_RESULTS = _('Poll Results')
LABEL_EDITS = _('Edits')
LABEL_NEW_SIGN_UPS = _('New Sign-Ups')
LABEL_NEW_REPORTS = _('New Reports')

POLL_DESKTOP_SUBTITLE = _('Polls you voted in or created')
STATUS_DESKTOP_SUBTITLE = _(
    'From accounts you turned notifications on for')
UPDATE_DESKTOP_SUBTITLE = _('Posts you boosted that were edited')
QUOTED_UPDATE_DESKTOP_SUBTITLE = _('Posts you quoted that were edited')
ADMIN_DESKTOP_SUBTITLE = _('Server administrators only')

# (type, label) for the header filter and the excluded-types settings.
FILTER_NOTIFICATION_TYPES = [
    ('mention', LABEL_MENTIONS),
    ('status', LABEL_NEW_POSTS),
    ('reblog', LABEL_BOOSTS),
    ('favourite', LABEL_FAVORITES),
    ('follow', LABEL_FOLLOWS),
    ('follow_request', LABEL_FOLLOW_REQUESTS),
    ('poll', LABEL_POLLS),
    ('update', LABEL_POST_EDITS),
    ('quote', LABEL_QUOTES),
    ('quoted_update', LABEL_QUOTE_EDITS),
    ('admin.sign_up', LABEL_SIGN_UPS),
    ('admin.report', LABEL_REPORTS),
    ('severed_relationships', LABEL_SEVERED_RELATIONSHIPS),
    ('moderation_warning', LABEL_MODERATION_WARNINGS),
    ('annual_report', LABEL_ANNUAL_REPORTS),
    ('added_to_collection', LABEL_ADDED_TO_COLLECTIONS),
    ('collection_update', LABEL_COLLECTION_UPDATES),
]

# (type, title, subtitle) rows in Preferences → Desktop notifications.
DESKTOP_NOTIFICATION_TYPES = (
    ('mention', LABEL_MENTIONS, None),
    ('follow', LABEL_NEW_FOLLOWERS, None),
    ('follow_request', LABEL_FOLLOW_REQUESTS, None),
    ('favourite', LABEL_FAVORITES, None),
    ('reblog', LABEL_BOOSTS, None),
    ('poll', LABEL_POLL_RESULTS, POLL_DESKTOP_SUBTITLE),
    ('status', LABEL_NEW_POSTS, STATUS_DESKTOP_SUBTITLE),
    ('update', LABEL_EDITS, UPDATE_DESKTOP_SUBTITLE),
    ('quote', LABEL_QUOTES, None),
    ('quoted_update', LABEL_QUOTE_EDITS, QUOTED_UPDATE_DESKTOP_SUBTITLE),
    ('admin.sign_up', LABEL_NEW_SIGN_UPS, ADMIN_DESKTOP_SUBTITLE),
    ('admin.report', LABEL_NEW_REPORTS, ADMIN_DESKTOP_SUBTITLE),
)
