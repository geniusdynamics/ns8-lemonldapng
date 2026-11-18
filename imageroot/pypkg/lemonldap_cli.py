# SPDX-License-Identifier: GPL-3.0-or-later
"""Run a configuration command without logging its secret arguments."""

import subprocess


def apply_settings(command, settings_name):
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise SystemExit(f"Could not update LemonLDAP::NG {settings_name} settings")
