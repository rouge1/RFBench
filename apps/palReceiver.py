#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#
# SPDX-License-Identifier: GPL-3.0
#
# PAL Receiver - tunes a System B, G or I television channel, detects the
# vestigial sideband the way a television does, decodes the 625-line PAL
# picture and plays the FM sound. The receiving half of
# ``palAnalogVideoRecorded``; the two share a tile in the launcher.
#
# It is ``ntscReceiver`` with the standard swapped: the window, the meters,
# Watch and the decoding thread are that receiver's, and only what BT.1701
# Table 1 makes different comes from ``apps/tv_system.py`` - where the
# carriers sit, the Nyquist slope's upper edge, the sound's deviation and
# de-emphasis - together with PAL's composite rate and the European channel
# plan.

import os
import sys

from PyQt5 import Qt  # type: ignore

from apps.ntsc_encode import PAL
from apps.ntscReceiver import ConfigDialog as NtscConfigDialog, ntscReceiver
from apps.ntscReceiver import main as _ntsc_main
from apps.tv_system import (PAL_CHOICES, AuralSound, VsbDemod,
                            pal_channel_centre, pal_channel_for_centre,
                            pal_channel_items, pal_system)
from apps.utils import FrequencyChooser

DEFAULT_CHANNEL = 'E21'
DEFAULT_SYSTEM = 'BG'
FREQ_MIN_MHZ = 45.0
FREQ_MAX_MHZ = 2200.0


class ConfigDialog(NtscConfigDialog):
    TITLE = "PAL Video Receiver"
    CONFIG_NAME = "palReceiver_config.json"
    SOUND_TIP = ("PAL sends the sound on its own FM carrier, 5.5 MHz above "
                 "the picture on B/G and 6.0 on I. Turning this off leaves "
                 "the picture untouched - the two are decoded by separate "
                 "chains.")

    def create_channel_control(self):
        self.layout.addWidget(Qt.QLabel("Television System:"))
        self.system_combo = Qt.QComboBox()
        for key, label in PAL_CHOICES:
            self.system_combo.addItem(label, key)
        self.layout.addWidget(self.system_combo)
        self.cf_chooser = FrequencyChooser(
            minimum=FREQ_MIN_MHZ, maximum=FREQ_MAX_MHZ,
            value=pal_channel_centre(DEFAULT_CHANNEL),
            channels=pal_channel_items())
        self.layout.addWidget(self.cf_chooser)

    def load_config(self):
        super().load_config()
        import json
        try:
            with open(self.config_file) as f:
                system = json.load(f).get('system', DEFAULT_SYSTEM)
        except Exception:
            system = DEFAULT_SYSTEM
        index = self.system_combo.findData(system)
        self.system_combo.setCurrentIndex(max(index, 0))

    def save_config(self):
        super().save_config()
        from apps.utils import update_app_config
        update_app_config(self.config_file,
                          {'system': self.system_combo.currentData()})

    def get_values(self):
        values = super().get_values()
        values['system'] = self.system_combo.currentData()
        return values


class palReceiver(ntscReceiver):
    TITLE = "PAL Video Receiver"
    SETTINGS_KEY = "palReceiver"
    dialog_class = ConfigDialog
    STANDARD = PAL
    # The spectrum shows the 8 MHz channel at half the radio's rate: flat
    # to 4.3 MHz either side of the centre and closed by 5.0.
    SPECTRUM_CUTOFF = 4.3e6
    SPECTRUM_TRANSITION = 0.7e6

    def __init__(self, config_values=None):
        if config_values is None:
            dialog = ConfigDialog()
            if not dialog.exec_():
                sys.exit(0)
            config_values = dialog.get_values()
        centre = float(config_values.get('center_mhz',
                                         pal_channel_centre(DEFAULT_CHANNEL)))
        # B or G by the channel the receiver opens on, as the transmitter
        # does; retuning keeps it.
        self.system = pal_system(config_values.get('system') or DEFAULT_SYSTEM,
                                 centre)
        self.LO_OFFSET = self.system.lo_offset
        vision = self.system.visual_carrier / 1e6
        self.SPECTRUM_TITLE = (
            f"Received Channel ({self.system.key}) - vision carrier "
            f"{-vision:g} MHz below channel centre, sound "
            f"{self.system.aural_spacing / 1e6:g} MHz above it")
        super().__init__(config_values)
        self.setWindowTitle(f"{self.TITLE} - {self.system.label}")

    def channel_items(self):
        return pal_channel_items()

    def channel_centre(self, channel):
        return pal_channel_centre(channel)

    def channel_for(self, mhz):
        return pal_channel_for_centre(mhz)

    def default_centre(self):
        return pal_channel_centre(DEFAULT_CHANNEL)

    def make_demod(self):
        return VsbDemod(self.samp_rate, self.system)

    def make_sound(self):
        return AuralSound(self.samp_rate, self.system, volume=self.volume,
                          tap=self.audio_tap)


def main(top_block_cls=palReceiver, options=None, app=None, config_values=None):
    return _ntsc_main(top_block_cls=top_block_cls, options=options, app=app,
                      config_values=config_values)


if __name__ == '__main__':
    main()
