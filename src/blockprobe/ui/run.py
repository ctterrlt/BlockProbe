# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scan run screen: source selection, preview and live progress (FR-6)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from blockprobe.core.models import RunSummary
from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import (
    ACCENT,
    ACCENT_DIM,
    BORDER,
    MUTED,
    SURFACE,
    WARN,
    apply_page_theme,
    button,
    card,
    hero,
    text,
)


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
    start_button: ft.Button | None
    pause_button: ft.Button | None
    resume_button: ft.Button | None
    cancel_button: ft.Button | None
    pause_slot: ft.Container | None
    resume_slot: ft.Container | None
    cancel_slot: ft.Container | None
    run_card: ft.Container | None
    run_card_title: ft.Text | None

    def _wrap(
        self, action: Callable[[], actions.ActionResult]
    ) -> Callable[[Any], Any]:
        raise NotImplementedError

    def _show_screen(self, index: int) -> None:
        raise NotImplementedError

    def _setup_run(self) -> None:
        self.source_checkboxes = {}
        self.sources_column = ft.Column(spacing=2)
        self.source_search = ft.TextField(
            label=t("Find a portal"),
            prefix_icon=ft.Icons.SEARCH,
            text_size=14,
            dense=True,
            on_change=self._filter_sources,
        )
        self._source_rows: dict[str, ft.Control] = {}
        self.preview_text = text(t("Preview not loaded yet."), 13)
        self.auto_refresh_switch = ft.Switch(value=True)
        self.progress_bar = ft.ProgressBar(value=0, color=ACCENT, bgcolor=ACCENT_DIM)
        self.progress_text = text(t("No scan is running."), 13)
        self._run_feedback_visible = False
        self._run_start_failed = False
        self._run_controls_signature: tuple[object, ...] | None = None
        self.summary_text = text(t("No run stored yet."), 13)
        self.start_button = None
        self.pause_button = None
        self.resume_button = None
        self.cancel_button = None
        self.pause_slot = None
        self.resume_slot = None
        self.cancel_slot = None
        self.run_card = None
        self.run_card_title = None

    def _run_view(self) -> ft.Control:
        self._build_source_checkboxes()
        self.start_button = button(
            t("Start scan"), ft.Icons.PLAY_ARROW, self._wrap(self._start_scan), primary=True
        )
        self.pause_button = button(t("Pause"), ft.Icons.PAUSE, self._wrap(actions.pause_scan))
        self.resume_button = button(t("Resume"), ft.Icons.PLAY_ARROW, self._wrap(actions.resume_scan))
        self.cancel_button = button(
            t("Cancel"), ft.Icons.CANCEL_OUTLINED, self._wrap(actions.cancel_scan)
        )
        self.pause_button.expand = True
        self.resume_button.expand = True
        self.cancel_button.expand = True
        self.pause_slot = ft.Container(
            content=self.pause_button,
            col={"xs": 12, "sm": 6},
            expand=True,
        )
        self.resume_slot = ft.Container(
            content=self.resume_button,
            col={"xs": 12, "sm": 6},
            expand=True,
        )
        self.cancel_slot = ft.Container(
            content=self.cancel_button,
            col={"xs": 12, "sm": 6},
            expand=True,
        )
        assert self.pause_slot is not None
        assert self.resume_slot is not None
        assert self.cancel_slot is not None
        self.refresh_run_controls()
        sources_card = card(
            t("1. Pick the sources"),
            ft.Column(
                [
                    self.source_search,
                    self.sources_column,
                    self.preview_text,
                    ft.ResponsiveRow(
                        [
                            ft.Container(
                                content=button(
                                    t("Preview"),
                                    ft.Icons.SEARCH,
                                    self._wrap(self._preview),
                                ),
                                col={"xs": 12, "sm": 4},
                            ),
                            ft.Container(
                                content=ft.Row(
                                    [
                                        self.auto_refresh_switch,
                                        ft.Container(
                                            content=text(
                                                t("Update stale portal lists first"),
                                                13,
                                            ),
                                            expand=True,
                                        ),
                                    ],
                                    spacing=8,
                                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                ),
                                col={"xs": 12, "sm": 8},
                            ),
                        ],
                        run_spacing=8,
                    ),
                    ft.Row(
                        [
                            text(
                                t(
                                    "Use custom sites for personalized checks, such as "
                                    "parental controls; scans keep sources separate."
                                ),
                                12,
                            ),
                            button(
                                t("Manage websites"),
                                ft.Icons.EDIT_LOCATION_ALT,
                                lambda _event: self._show_screen(1),
                            ),
                        ],
                        wrap=True,
                        spacing=10,
                    ),
                ],
                spacing=10,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        run_card = card(
            t("2. Run the check"),
            ft.Column(
                [
                    ft.ResponsiveRow(
                        [
                            ft.Container(content=self.start_button, col={"xs": 12, "sm": 6}),
                            self.pause_slot,
                            self.resume_slot,
                            self.cancel_slot,
                        ],
                        run_spacing=8,
                    ),
                    self.progress_bar,
                    self.progress_text,
                ],
                spacing=12,
                horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            ),
        )
        self.run_card = run_card
        self.run_card_title = run_card.content.controls[0]  # type: ignore[union-attr]
        return ft.Column(
            [
                hero(
                    t("Scan this device's network path"),
                    t("Choose regulator lists or custom sites for a focused filter check."),
                    icon=ft.Icons.RADAR,
                ),
                sources_card,
                run_card,
                card(t("Latest stored run"), self.summary_text),
                # Keep the last card fully reachable above the mobile
                # navigation and rounded window edges.
                ft.Container(height=16),
            ],
            spacing=12,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            expand=True,
        )

    def refresh_run_layout(self, compact: bool) -> None:
        """Tighten the persistent scan action on narrow phone layouts."""
        if self.run_card is None or self.run_card_title is None:
            return
        self.run_card_title.visible = True
        self.run_card.padding = (
            ft.Padding.symmetric(horizontal=12, vertical=8)
            if compact
            else ft.Padding.symmetric(horizontal=16, vertical=14)
        )

    def refresh_run_controls(
        self,
        summary: RunSummary | None = None,
        active: bool | None = None,
    ) -> bool:
        if (
            self.start_button is None
            or self.pause_button is None
            or self.resume_button is None
            or self.cancel_button is None
            or self.pause_slot is None
            or self.resume_slot is None
            or self.cancel_slot is None
        ):
            return False
        if summary is None:
            summary = actions.latest_run_summary()
        if active is None:
            active = actions.runtime().scan.active_run is not None
        status = summary.status.value if summary is not None else ""
        running = active and status == "running"
        paused = status == "paused"
        signature = (active, status, running, paused, self._run_feedback_visible)
        if signature == self._run_controls_signature:
            return False
        self._run_controls_signature = signature
        self.start_button.visible = not active
        self.start_button.disabled = active
        self.pause_button.visible = running
        self.resume_button.visible = paused
        self.cancel_button.visible = running
        # Hide the responsive cells too; otherwise an invisible Pause/Resume
        # button still reserves half a phone row and pushes its counterpart off-screen.
        self.pause_slot.visible = running
        self.resume_slot.visible = paused
        self.cancel_slot.visible = running
        # Keep feedback visible after the click handler returns too. On compact
        # layouts this is the only in-context place where a failed start can be
        # explained; hiding it made the Start button appear to do nothing.
        show_progress = active or status == "paused" or self._run_feedback_visible
        self.progress_bar.visible = show_progress
        self.progress_text.visible = show_progress
        return True

    def _build_source_checkboxes(self) -> None:
        choices = actions.portal_choices()
        controls: list[ft.Control] = []
        self.source_checkboxes = {}
        self._source_rows = {}
        all_box = ft.Checkbox(
            value=True,
            semantics_label=t("All sources (every enabled portal + custom)"),
        )
        all_box.data = "all"
        custom_box = ft.Checkbox(
            value=False, semantics_label=t("Custom websites")
        )
        custom_box.data = "custom"
        self.source_checkboxes["all"] = all_box
        self.source_checkboxes["custom"] = custom_box
        row = self._source_choice_row(
            "all", all_box, t("All sources (every enabled portal + custom)")
        )
        controls.append(row)
        self._source_rows["all"] = row
        for portal_id, label in choices:
            box = ft.Checkbox(value=False, semantics_label=label)
            box.data = portal_id
            self.source_checkboxes[portal_id] = box
            row = self._source_choice_row(portal_id, box, label)
            controls.append(row)
            self._source_rows[portal_id] = row
        row = self._source_choice_row("custom", custom_box, t("Custom websites"))
        controls.append(row)
        self._source_rows["custom"] = row
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
        preview = actions.preview_scan(self._request())
        self.preview_text.value = preview.detail

    def _filter_sources(self, event: Any) -> None:
        query = str(event.control.value or "").strip().casefold()
        for key, row in self._source_rows.items():
            if key in {"all", "custom"}:
                row.visible = not query
                continue
            label = self.source_checkboxes[key].semantics_label or key
            row.visible = not query or query in label.casefold()
        self.page.update()

    def _source_choice_row(
        self, key: str, checkbox: ft.Checkbox, label: str
    ) -> ft.Control:
        def toggle(_event: Any) -> None:
            checkbox.value = not bool(checkbox.value)
            self._apply_source_change(key, bool(checkbox.value))

        return ft.Container(
            content=ft.Row(
                [
                    checkbox,
                    ft.Container(content=text(label, 14), expand=True, on_click=toggle),
                    ft.Icon(ft.Icons.CHEVRON_RIGHT, color=MUTED, size=18),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            padding=ft.Padding.symmetric(horizontal=10, vertical=3),
            bgcolor=ACCENT_DIM if checkbox.value else SURFACE,
            border=ft.Border.all(1, BORDER),
            border_radius=10,
        )

    def _on_source_change(self, event: Any) -> None:
        changed = str(event.control.data or "")
        self._apply_source_change(changed, bool(event.control.value))

    def _apply_source_change(self, changed: str, selected: bool) -> None:
        checkboxes = self.source_checkboxes
        if changed == "all":
            if selected:
                for key, box in checkboxes.items():
                    if key != "all":
                        box.value = False
        else:
            if selected:
                checkboxes["all"].value = False
            if not any(box.value for key, box in checkboxes.items() if key != "all"):
                checkboxes["all"].value = True
        for key, row in self._source_rows.items():
            if isinstance(row, ft.Container):
                row.bgcolor = ACCENT_DIM if checkboxes[key].value else SURFACE
        apply_page_theme(
            self.page,
            actions.runtime().settings.config.app.theme_mode,
            update=False,
        )
        self._refresh_preview()
        self.page.update()

    def _preview(self) -> actions.ActionResult:
        return self._refresh_preview()

    def _refresh_preview(self) -> actions.ActionResult:
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
        self._run_feedback_visible = True
        self._run_start_failed = False
        self.progress_text.value = t("Preparing scan and updating source lists if needed…")
        self.progress_bar.value = None
        self.refresh_run_controls()
        if self.start_button is not None:
            self.start_button.disabled = True
        try:
            self.page.update()
        except Exception:
            pass
        request = self._request()
        result = actions.start_scan(request)
        if result.ok:
            self.progress_text.value = result.title
            self.progress_bar.value = None
        else:
            self._run_start_failed = True
            self.progress_text.value = result.detail
            self.progress_bar.value = 0
            if self.start_button is not None:
                self.start_button.disabled = False
        return result
