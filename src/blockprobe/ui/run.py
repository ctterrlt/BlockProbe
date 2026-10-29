# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan run screen: source selection, preview and live progress (FR-6)."""

from __future__ import annotations

import flet as ft

from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import WARN, button, card, hero, text


class RunScreen:
    """Mixin with the run screen controls and handlers.

    The host class provides ``page``, ``_wrap`` and ``run_async``.
    """

    page: ft.Page
    source_checkboxes: dict[str, ft.Checkbox]
    sources_column: ft.Column
    preview_text: ft.Text
    auto_refresh_switch: ft.Switch
    progress_bar: ft.ProgressBar
    progress_text: ft.Text
    summary_text: ft.Text

    def _wrap(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _setup_run(self) -> None:
        self.source_checkboxes = {}
        self.sources_column = ft.Column(spacing=2)
        self.preview_text = text(t("Preview not loaded yet."), 13)
        self.auto_refresh_switch = ft.Switch(
            label=t("Update stale portal lists first"), value=True
        )
        self.progress_bar = ft.ProgressBar(value=0, color="#38BDF8", bgcolor="#1E293B")
        self.progress_text = text(t("No scan is running."), 13)
        self.summary_text = text(t("No run stored yet."), 13)

    def _run_view(self) -> ft.Control:
        self._build_source_checkboxes()
        return ft.Column(
            [
                hero(
                    t("Scan this device's network path"),
                    t("Check whether protection is active without opening websites in a browser."),
                    icon=ft.Icons.RADAR,
                ),
                card(
                    t("1. Pick the sources"),
                    ft.Column(
                        [
                            self.sources_column,
                            self.preview_text,
                            ft.Row(
                                [
                                    button(
                                        t("Preview"),
                                        ft.Icons.SEARCH,
                                        self._wrap(self._preview),
                                    ),
                                    self.auto_refresh_switch,
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
                card(
                    t("2. Run the check"),
                    ft.Column(
                        [
                            ft.Row(
                                [
                                    button(
                                        t("Start scan"),
                                        ft.Icons.PLAY_ARROW,
                                        self._wrap(self._start_scan),
                                        primary=True,
                                    ),
                                    button(
                                        t("Pause"),
                                        ft.Icons.PAUSE,
                                        self._wrap(actions.pause_scan),
                                    ),
                                    button(
                                        t("Resume"),
                                        ft.Icons.PLAY_ARROW,
                                        self._wrap(actions.resume_scan),
                                    ),
                                    button(
                                        t("Cancel"),
                                        ft.Icons.CANCEL_OUTLINED,
                                        self._wrap(actions.cancel_scan),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                            self.progress_bar,
                            self.progress_text,
                        ],
                        spacing=12,
                    ),
                ),
                card(t("Latest stored run"), self.summary_text),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _build_source_checkboxes(self) -> None:
        choices = actions.portal_choices()
        controls: list[ft.Control] = []
        self.source_checkboxes = {}
        all_box = ft.Checkbox(
            label=t("All sources (every enabled portal + custom)"), value=True
        )
        all_box.data = "all"
        custom_box = ft.Checkbox(label=t("Custom websites"), value=False)
        custom_box.data = "custom"
        self.source_checkboxes["all"] = all_box
        self.source_checkboxes["custom"] = custom_box
        controls.append(all_box)
        for portal_id, label in choices:
            box = ft.Checkbox(label=label, value=False)
            box.data = portal_id
            self.source_checkboxes[portal_id] = box
            controls.append(box)
        controls.append(custom_box)
        for box in self.source_checkboxes.values():
            box.on_change = self._on_source_change
        self.sources_column.controls = controls
        if not choices:
            controls.insert(
                1,
                text(
                    t("No portal definitions found. Add one under the portals directory."),
                    12,
                    WARN,
                ),
            )

    def _on_source_change(self, event: ft.ControlEvent) -> None:
        changed = str(event.control.data or "")
        checkboxes = self.source_checkboxes
        if changed == "all":
            if checkboxes["all"].value:
                for key, box in checkboxes.items():
                    if key != "all":
                        box.value = False
        else:
            if event.control.value:
                checkboxes["all"].value = False
            if not any(box.value for key, box in checkboxes.items() if key != "all"):
                checkboxes["all"].value = True
        self.page.update()

    def _preview(self) -> actions.ActionResult:
        result = actions.preview_scan(self._request())
        self.preview_text.value = result.detail
        return result

    def _request(self) -> actions.ScanRequest:
        checkboxes = self.source_checkboxes
        if checkboxes.get("all") is not None and checkboxes["all"].value:
            return actions.ScanRequest(
                all_sources=True,
                with_custom=True,
                auto_refresh=bool(self.auto_refresh_switch.value),
            )
        portal_ids = tuple(
            key
            for key, box in checkboxes.items()
            if key not in {"all", "custom"} and box.value
        )
        return actions.ScanRequest(
            all_sources=False,
            portal_ids=portal_ids,
            with_custom=bool(checkboxes.get("custom") and checkboxes["custom"].value),
            auto_refresh=bool(self.auto_refresh_switch.value),
        )

    def _start_scan(self) -> actions.ActionResult:
        request = self._request()
        result = actions.start_scan(request)
        if result.ok:
            self.progress_text.value = t("Scan started. Progress follows automatically.")
            self.progress_bar.value = None
        return result
