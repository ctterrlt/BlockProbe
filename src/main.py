# SPDX-License-Identifier: AGPL-3.0-or-later
"""Flet application entry point for BlockProbe."""

import flet as ft

from blockprobe.ui.app import main

ft.run(main, assets_dir="assets")
