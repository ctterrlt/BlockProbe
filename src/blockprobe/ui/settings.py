# SPDX-License-Identifier: AGPL-3.0-or-later
"""Settings screen: probe options and the guarded danger zone (FR-14)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from blockprobe.i18n import DEFAULT_LANGUAGE, available_languages, t
from blockprobe.ui import actions
from blockprobe.ui.components import BACKGROUND, BAD, TEXT, button, card, hero, text

_LANGUAGE_FLAGS = {"it": "🇮🇹", "en": "🇬🇧", "fr": "🇫🇷", "es": "🇪🇸", "pt": "🇵🇹"}


class SettingsScreen:
    """Mixin with the settings screen and the confirmation dialogs."""

    page: ft.Page
    debug_switch: ft.Switch
    language_dropdown: ft.Dropdown
    concurrency_field: ft.TextField
    timeout_field: ft.TextField
    control_url_field: ft.TextField
    auto_refresh_setting: ft.Switch
    ttl_field: ft.TextField
    settings_status: ft.Text

    def _wrap(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def run_async(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def rebuild_ui(self, result: actions.ActionResult | None = None) -> None:
        raise NotImplementedError

    def _setup_settings(self) -> None:
        config = actions.runtime().settings.config
        self.debug_switch = ft.Switch(label=t("Debug logging"), value=config.app.debug)
        self.language_dropdown = ft.Dropdown(
            label=t("Language"),
            value=config.app.language,
            width=170,
            dense=True,
            options=[
                ft.DropdownOption(key=code, text=f"{_LANGUAGE_FLAGS[code]} {name}")
                for code, name in available_languages().items()
            ],
        )
        self.concurrency_field = ft.TextField(
            label=t("Parallel websites (desktop)"),
            value=str(config.probe.concurrency),
            width=220,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self.timeout_field = ft.TextField(
            label=t("Timeout (seconds)"),
            value=str(config.probe.timeout_seconds),
            width=180,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self.control_url_field = ft.TextField(
            label=t("Connectivity control URL"),
            value=config.probe.control_url,
            width=340,
            dense=True,
        )
        self.auto_refresh_setting = ft.Switch(
            label=t("Update stale portal lists before a run"),
            value=config.portals.auto_refresh_on_run,
        )
        self.ttl_field = ft.TextField(
            label=t("Portal update window (hours)"),
            value=str(config.portals.refresh_ttl_hours),
            width=240,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self.schedule_frequency = ft.Dropdown(
            label=t("Frequency"),
            value="weekly",
            width=150,
            dense=True,
            options=[
                ft.DropdownOption(key="weekly", text=t("Weekly")),
                ft.DropdownOption(key="daily", text=t("Daily")),
            ],
        )
        self.schedule_time = ft.TextField(
            label=t("Time (HH:MM)"),
            value="20:00",
            width=150,
            dense=True,
        )
        self.schedule_weekday = ft.TextField(
            label=t("Weekday (1=Mon .. 7=Sun)"),
            value="1",
            width=200,
            dense=True,
            keyboard_type=ft.KeyboardType.NUMBER,
        )
        self.schedule_status = text("", 12)
        self._refresh_schedule_status()
        self.settings_status = text("", 12)

    def _refresh_schedule_status(self) -> None:
        try:
            from blockprobe.scheduler import status as job_status

            info = job_status()
            state = t("installed") if info.installed else t("not installed")
        except Exception:
            state = t("unknown")
        self.schedule_status.value = t(
            "Scheduled scan: {state}. Off by default, runs as your user, no admin rights.",
            state=state,
        )

    def _settings_view(self) -> ft.Control:
        return ft.Column(
            [
                hero(
                    t("Tune your checks"),
                    t("Manage network checks, updates and local data from one place."),
                    icon=ft.Icons.TUNE,
                ),
                card(
                    t("Probe settings"),
                    ft.Column(
                        [
                            self.debug_switch,
                            ft.Row(
                                [self.language_dropdown, self.concurrency_field],
                                wrap=True,
                                spacing=12,
                            ),
                            ft.Row(
                                [self.timeout_field, self.control_url_field],
                                wrap=True,
                                spacing=12,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
                card(
                    t("Portal updates"),
                    ft.Column(
                        [
                            self.auto_refresh_setting,
                            self.ttl_field,
                            button(
                                t("Save settings"),
                                ft.Icons.SAVE,
                                self._wrap(self._save_settings),
                                primary=True,
                            ),
                            self.settings_status,
                        ],
                        spacing=10,
                    ),
                ),
                card(
                    t("Scheduled scans (off by default)"),
                    ft.Column(
                        [
                            text(
                                t(
                                    "Writes user-level job files (systemd user timer, launchd "
                                    "agent or Task Scheduler XML). Nothing is enabled until you "
                                    "run the command shown in the confirmation."
                                ),
                                12,
                            ),
                            ft.Row(
                                [
                                    self.schedule_frequency,
                                    self.schedule_time,
                                    self.schedule_weekday,
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                            ft.Row(
                                [
                                    button(
                                        t("Write schedule"),
                                        ft.Icons.SCHEDULE,
                                        self._wrap(self._install_schedule),
                                    ),
                                    button(
                                        t("Remove schedule"),
                                        ft.Icons.EVENT_BUSY,
                                        self._wrap(self._remove_schedule),
                                    ),
                                    button(
                                        t("Refresh state"),
                                        ft.Icons.VISIBILITY,
                                        self._wrap(self._schedule_state_action),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                            self.schedule_status,
                        ],
                        spacing=10,
                    ),
                ),
                card(
                    t("Danger zone"),
                    ft.Column(
                        [
                            text(
                                t(
                                    "The website list is append-only. Reset is the only way "
                                    "to remove portal entries; a backup is written first."
                                ),
                                12,
                            ),
                            ft.Row(
                                [
                                    button(
                                        t("Reset website list"),
                                        ft.Icons.RESTART_ALT,
                                        self._wrap(self._confirm_reset),
                                    ),
                                    button(
                                        t("Delete all my data"),
                                        ft.Icons.DELETE_FOREVER,
                                        self._wrap(self._confirm_delete),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def _save_settings(self) -> actions.ActionResult:
        try:
            concurrency = int(self.concurrency_field.value or "0")
            timeout = int(self.timeout_field.value or "0")
            ttl = int(self.ttl_field.value or "0")
        except ValueError:
            return actions.ActionResult(
                t("Settings not saved"),
                t("Concurrency, timeout and hours must be numbers."),
                ok=False,
            )
        previous_language = actions.runtime().settings.config.app.language
        result = actions.save_settings(
            debug=bool(self.debug_switch.value),
            language=str(self.language_dropdown.value or DEFAULT_LANGUAGE),
            concurrency=concurrency,
            timeout_seconds=timeout,
            control_url=self.control_url_field.value or "",
            auto_refresh_on_run=bool(self.auto_refresh_setting.value),
            refresh_ttl_hours=ttl,
        )
        if (
            result.ok
            and actions.runtime().settings.config.app.language != previous_language
        ):
            self.rebuild_ui(result)
        else:
            self.settings_status.value = result.detail
        return result

    def _install_schedule(self) -> actions.ActionResult:
        try:
            weekday = int(self.schedule_weekday.value or "1")
        except ValueError:
            return actions.ActionResult(
                t("Schedule not installed"),
                t("The weekday must be a number between 1 and 7."),
                ok=False,
            )
        result = actions.install_schedule(
            frequency=str(self.schedule_frequency.value or "weekly"),
            time=self.schedule_time.value or "20:00",
            weekday=weekday,
        )
        self._refresh_schedule_status()
        return result

    def _remove_schedule(self) -> actions.ActionResult:
        result = actions.remove_schedule()
        self._refresh_schedule_status()
        return result

    def _schedule_state_action(self) -> actions.ActionResult:
        self._refresh_schedule_status()
        return actions.schedule_status()

    def _confirm_reset(self) -> actions.ActionResult:
        safety = actions.runtime().settings.config.safety
        self._show_confirm_dialog(
            t("Reset the website list"),
            t(
                "Type {expected} to remove the stored portal websites. "
                "A backup is written first. Custom websites are kept.",
                expected=repr(safety.reset_confirm_word),
            ),
            safety.reset_confirm_word,
            lambda value: actions.reset_website_list(value, include_custom=False),
        )
        return actions.ActionResult(
            t("Confirmation needed"), t("Use the dialog to confirm the reset.")
        )

    def _confirm_delete(self) -> actions.ActionResult:
        safety = actions.runtime().settings.config.safety
        self._show_confirm_dialog(
            t("Delete all local data"),
            t(
                "Type {expected} to erase runs, results, the website "
                "list, custom websites and the consent record.",
                expected=repr(safety.reset_all_confirm_word),
            ),
            safety.reset_all_confirm_word,
            actions.delete_all_data,
        )
        return actions.ActionResult(
            t("Confirmation needed"), t("Use the dialog to confirm the deletion.")
        )

    def _show_confirm_dialog(
        self,
        title: str,
        message: str,
        expected: str,
        action: Callable[[str], actions.ActionResult],
    ) -> None:
        field = ft.TextField(label=t("Type {expected}", expected=expected), autofocus=True)

        def confirm(_event: Any) -> None:
            value = field.value or ""
            self.page.pop_dialog()
            self.run_async(lambda: action(value))

        def cancel(_event: Any) -> None:
            self.page.pop_dialog()

        dialog = ft.AlertDialog(
            modal=True,
            title=text(title, 16, TEXT, weight=ft.FontWeight.BOLD),
            content=ft.Column([text(message, 13), field], tight=True, spacing=10),
            actions=[
                ft.TextButton(content=t("Cancel"), on_click=cancel),
                ft.Button(
                    content=t("Confirm"),
                    bgcolor=BAD,
                    color=BACKGROUND,
                    on_click=confirm,
                ),
            ],
        )
        self.page.show_dialog(dialog)
