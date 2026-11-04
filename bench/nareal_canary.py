#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check that Na Real's public pages still have the elements the Na Real
plugin (plugins/nareal/babble_nareal.py) relies on. Run by hand, never
by the test suite and never installed:

    python3 bench/nareal_canary.py [--server URL] [--handle @user@host]

It fetches the login page and an ask page, both public, and parses them
with html.parser: the login form (``<form method=post>`` holding
``#instance``) and the ask form (``form.question-box`` holding
``textarea#question-input`` named ``question``). Prints ``ok - ...`` or
``FAIL - ...`` per check; exits 0, 1 when a check failed, 2 when a page
could not be fetched.

The plugin itself relies on the login form only. The ask-form checks
stay as a cheap sign that the live site's markup is still the one this
was written against; the plugin no longer scripts that page. What it
does rely on is behind a login and is NOT checked here: the answer link
on the dashboard and the archive, and the navbar's share button
(``nav.navbar .navbar-end button.button``) and the ``url`` and
``question_title`` fields of the settings page.
"""

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

DEFAULT_SERVER = 'https://na-real.bolha.dev'
DEFAULT_HANDLE = '@mauriciobc@ursal.zone'
TIMEOUT = 20


class Page(HTMLParser):
    """Collects every start tag with its attributes, and which forms
    each tag sits inside of."""

    def __init__(self):
        super().__init__()
        self.tags = []  # (tag, attrs dict, tuple of enclosing form attrs)
        self.forms = []  # attrs of the forms currently open

    def handle_starttag(self, tag, attrs):
        attrs = {name: value or '' for name, value in attrs}
        if tag == 'form':
            self.forms.append(attrs)
        self.tags.append((tag, attrs, tuple(self.forms)))

    def handle_endtag(self, tag):
        if tag == 'form' and self.forms:
            self.forms.pop()

    def find(self, tag, **wanted):
        return [(attrs, forms) for found, attrs, forms in self.tags
                if found == tag and all(
                    attrs.get(name) == value
                    for name, value in wanted.items())]


def fetch(url):
    request = urllib.request.Request(
        url, headers={'Accept': 'text/html',
                      'User-Agent': 'babble-nareal-canary'})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read().decode('utf-8', 'replace')


def parse(html):
    page = Page()
    page.feed(html)
    page.close()
    return page


failures = []


def check(name, condition):
    print('%s - %s' % ('ok' if condition else 'FAIL', name))
    if not condition:
        failures.append(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--server', default=DEFAULT_SERVER)
    parser.add_argument('--handle', default=DEFAULT_HANDLE)
    args = parser.parse_args()
    server = args.server.rstrip('/')
    ask = '%s/%s' % (server, urllib.parse.quote(args.handle, safe='@'))
    try:
        login = parse(fetch(server + '/login'))
        asked = parse(fetch(ask))
    except (urllib.error.URLError, OSError) as error:
        print('network error: %s' % error)
        return 2

    fields = login.find('input', id='instance')
    check('/login has an input#instance', len(fields) == 1)
    check('/login: #instance is inside a form', bool(fields)
          and bool(fields[0][1]))
    check('/login: that form is method=post', bool(fields) and bool(
        fields[0][1])
          and fields[0][1][-1].get('method', '').lower() == 'post')
    check('/login: #instance is empty when served', bool(fields)
          and not fields[0][0].get('value'))

    boxes = asked.find('textarea', id='question-input')
    check('%s has a textarea#question-input' % args.handle,
          len(boxes) == 1)
    check('the textarea is named "question"', bool(boxes)
          and boxes[0][0].get('name') == 'question')
    check('the textarea is inside form.question-box', bool(boxes) and any(
        'question-box' in form.get('class', '').split()
        for form in boxes[0][1]))
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
