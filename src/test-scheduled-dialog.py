#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""The schedule picker: its two time rows and the time it builds.

The hour and minute are AdwSpinRows rather than spin buttons hung off an
ActionRow, which moved both the ranges and the value API the dialog
reads (``get_value``; AdwSpinRow has no ``get_value_as_int``). Both are
easy to get wrong in a way no screenshot shows, so they are read back
here.

Exits 77 (skip) when there is no display."""

import datetime
import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib  # noqa: E402

if not os.environ.get('DISPLAY'):
    print('SKIP - no display available')
    sys.exit(77)

srcdir = os.environ.get('BABBLE_SRCDIR') or os.getcwd()
sys.path.insert(0, os.path.dirname(os.path.abspath(srcdir)))
sys.path.insert(0, srcdir)

from src.scheduled import SchedulePickerDialog  # noqa: E402

failures = []


def check(name, condition, detail=''):
    if condition:
        print('ok - %s' % name)
    else:
        print('FAIL - %s%s' % (name, (' [%s]' % detail) if detail else ''))
        failures.append(name)


dialog = SchedulePickerDialog()

check('the hour row is an AdwSpinRow',
      isinstance(dialog.hour_spin, Adw.SpinRow),
      type(dialog.hour_spin).__name__)
check('the minute row is an AdwSpinRow',
      isinstance(dialog.minute_spin, Adw.SpinRow),
      type(dialog.minute_spin).__name__)

hour_adjustment = dialog.hour_spin.get_adjustment()
minute_adjustment = dialog.minute_spin.get_adjustment()
check('the hour row runs 0 to 23',
      (hour_adjustment.get_lower(), hour_adjustment.get_upper(),
       hour_adjustment.get_step_increment()) == (0.0, 23.0, 1.0),
      repr(hour_adjustment.get_lower()) + '..' +
      repr(hour_adjustment.get_upper()))
check('the minute row runs 0 to 59',
      (minute_adjustment.get_lower(), minute_adjustment.get_upper(),
       minute_adjustment.get_step_increment()) == (0.0, 59.0, 1.0),
      repr(minute_adjustment.get_lower()) + '..' +
      repr(minute_adjustment.get_upper()))
check('both rows snap to whole steps',
      dialog.hour_spin.get_snap_to_ticks()
      and dialog.minute_spin.get_snap_to_ticks())

# The value the dialog builds is read with get_value(); if that ever goes
# back to a widget API this stops being an int and the datetime blows up.
check('the rows start in range',
      0 <= int(dialog.hour_spin.get_value()) <= 23
      and 0 <= int(dialog.minute_spin.get_value()) <= 59,
      '%s:%s' % (dialog.hour_spin.get_value(),
                  dialog.minute_spin.get_value()))

chosen = dialog._chosen()
check('the chosen time is a real datetime', isinstance(chosen, GLib.DateTime),
      type(chosen).__name__)
check('the chosen time carries the rows\' values',
      chosen.get_hour() == int(dialog.hour_spin.get_value())
      and chosen.get_minute() == int(dialog.minute_spin.get_value()),
      '%02d:%02d vs %02d:%02d' % (chosen.get_hour(), chosen.get_minute(),
                                  int(dialog.hour_spin.get_value()),
                                  int(dialog.minute_spin.get_value())))

# Setting the rows revalidates, which is what keeps the accept button
# honest about the five-minute lead.
dialog.hour_spin.set_value(int(dialog.hour_spin.get_value()) % 24)
dialog.minute_spin.set_value((int(dialog.minute_spin.get_value()) + 7) % 60)
check('changing a row keeps the chosen time in step',
      dialog._chosen().get_hour() == int(dialog.hour_spin.get_value())
      and dialog._chosen().get_minute() == int(dialog.minute_spin.get_value()))

# A date far enough out is always valid, whatever the rows say.
soon = (datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=2)).replace(microsecond=0)
iso = soon.strftime('%Y-%m-%dT%H:%M:%S+00:00')
far = SchedulePickerDialog(initial_iso=iso)
check('a date two days out is accepted',
      far.schedule_button.get_sensitive()
      and not far.hint.get_visible(),
      'sensitive=%s' % far.schedule_button.get_sensitive())

if failures:
    print('\n%d schedule check(s) failed: %s'
          % (len(failures), ', '.join(failures)))
    sys.exit(1)
print('\nall schedule picker checks passed')
