#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import json
from PyQt5.QtWidgets import (  # type: ignore
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QVBoxLayout)
from PyQt5.QtCore import Qt # type: ignore

from apps.utils import geometry_is_reachable, read_settings, update_app_config


def valid_ip(ip):
    """A dotted IPv4 address a radio could have: four octets of 0-255, and
    neither the unspecified address nor the broadcast one."""
    octets = ip.split('.')
    if len(octets) != 4:
        return False
    if not all(o.isdigit() and 0 <= int(o) <= 255 for o in octets):
        return False
    return ip not in ("0.0.0.0", "255.255.255.255")


class SettingsDialog(QDialog):
    """The gear in the launcher's header: where media is, which radio is
    connected, and - for an Ettus USRP, the one radio on the network rather
    than USB - its IP address.

    There was also a Single/Multi launcher mode and a list of IP addresses,
    for driving several networked USRPs at once. That was never used or
    tested, and is gone; the launcher always hides while an app runs.
    """

    def __init__(self, settings_file, parent=None):
        super().__init__(parent)
        self.settings_file = settings_file
        self.setWindowTitle("Settings")
        self.setWindowFlags(Qt.Window)
        self.setMinimumWidth(480)

        settings = read_settings(settings_file)
        layout = QVBoxLayout(self)

        # Media
        media_group = QGroupBox("Media Directory")
        media_layout = QHBoxLayout()
        self.media_path = QLineEdit(settings.get('media_directory', ''))
        browse_btn = QPushButton("Browse")
        # Enter saves the dialog; it should not open a file picker.
        browse_btn.setAutoDefault(False)
        browse_btn.clicked.connect(self.browse_media_dir)
        media_layout.addWidget(self.media_path)
        media_layout.addWidget(browse_btn)
        media_group.setLayout(media_layout)

        # Radio, and the Ettus's address
        radio_group = QGroupBox("Radio")
        radio_layout = QVBoxLayout()
        self.radio_hw_combo = QComboBox()
        self.radio_hw_combo.addItem("HackRF One (USB)", "hackrf")
        self.radio_hw_combo.addItem("Ettus USRP (Network)", "usrp")
        # The two Signal Hound instruments are one-way: the VSG60 only
        # transmits and the BB60D only receives, so choosing one of them
        # makes the apps in the other direction say so and launch nothing.
        self.radio_hw_combo.addItem("Signal Hound VSG60 (USB, transmit only)",
                                    "vsg")
        self.radio_hw_combo.addItem("Signal Hound BB60D (USB, receive only)",
                                    "bb60")
        radio_index = self.radio_hw_combo.findData(
            settings.get('radio_type', 'hackrf'))
        self.radio_hw_combo.setCurrentIndex(max(radio_index, 0))
        # The list it drops down is painted by the dialog's stylesheet, in
        # whichever theme is in force. It had grey of its own, left over
        # from before there was a theme, which on Reading Room's paper
        # dropped a dark box out of a light dialog.
        radio_layout.addWidget(self.radio_hw_combo)

        # Only the Ettus is on the network, so only it has an address. The
        # row stays in place for the others, greyed out, so it is plain
        # where the address goes - and one typed in is kept.
        ip_row = QHBoxLayout()
        self.ip_label = QLabel("Ettus IP Address:")
        self.ip_input = QLineEdit(settings.get('usrp_ip', ''))
        self.ip_input.setPlaceholderText("192.168.10.2")
        ip_row.addWidget(self.ip_label)
        ip_row.addWidget(self.ip_input)
        radio_layout.addLayout(ip_row)
        radio_group.setLayout(radio_layout)
        self.radio_hw_combo.currentIndexChanged.connect(self.update_ip_state)
        self.update_ip_state()

        buttons = QDialogButtonBox(QDialogButtonBox.Save
                                   | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(media_group)
        layout.addWidget(radio_group)
        layout.addWidget(buttons)

        # Put it back where it was left. Only the width comes back with it:
        # the height is whatever the contents need, and one saved from the
        # longer dialog this replaced would leave a gap in the middle.
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r') as f:
                    pos = json.load(f).get('settings_dialog_position')
                # Unlike the other two restore sites this one never checked
                # anything, so it would happily put the dialog somewhere the
                # screen no longer reaches - the opposite failure, and just as
                # awkward once a monitor is unplugged.
                if pos and geometry_is_reachable(QApplication.instance(), pos):
                    self.resize(pos['width'], self.sizeHint().height())
                    self.move(pos['x'], pos['y'])
        except Exception as e:
            print(f"Error restoring settings dialog geometry: {e}")

    def is_usrp(self):
        return self.radio_hw_combo.currentData() == 'usrp'

    def update_ip_state(self):
        self.ip_label.setEnabled(self.is_usrp())
        self.ip_input.setEnabled(self.is_usrp())

    def browse_media_dir(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Select Media Directory",
            self.media_path.text(),
            QFileDialog.ShowDirsOnly
        )
        if directory:
            self.media_path.setText(directory)

    def accept(self):
        ip = self.ip_input.text().strip()
        if self.is_usrp() and not valid_ip(ip):
            QMessageBox.warning(
                self, "Ettus IP Address",
                "The Ettus USRP needs its IP address, as four numbers "
                "from 0 to 255 separated by dots - 192.168.10.2, say."
                if not ip else f"{ip} is not an IP address.")
            self.ip_input.setFocus()
            return
        if not valid_ip(ip):
            ip = ''                 # greyed out, and nothing to keep

        # The launcher keeps its window position and theme in the same
        # file, so this merges rather than writes it whole - and drops the
        # two keys the multi-radio launcher kept.
        try:
            update_app_config(self.settings_file, {
                'media_directory': self.media_path.text(),
                'radio_type': self.radio_hw_combo.currentData(),
                'usrp_ip': ip,
            }, remove=('ip_addresses', 'radio_mode'))
        except Exception as e:
            print(f"Error saving settings: {e}")

        super().accept()
