# SPDX-License-Identifier: AGPL-3.0-or-later
"""Results table, filters, comparison and exports (FR-8, FR-10)."""

from __future__ import annotations

from collections.abc import Callable

import flet as ft

from blockprobe.core.enums import Verdict
from blockprobe.core.models import ResultRow
from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import (
    BAD,
    BORDER,
    MUTED,
    OK,
    SURFACE,
    TEXT,
    WARN,
    apply_page_theme,
    button,
    card,
    hero,
    text,
)


class ResultsScreen:
    """Mixin with the results screen and its filters."""

    RESULTS_PAGE_SIZE = 20

    page: ft.Page
    results_summary: ft.Text
    results_search: ft.TextField
    verdict_filter: ft.Dropdown
    leaks_only_switch: ft.Switch
    results_table: ft.DataTable
    results_scroll: ft.Row
    results_mobile_list: ft.Column
    results_empty: ft.Container
    comparison_text: ft.Text
    results_page_label: ft.Text
    export_status: ft.Text

    def _wrap(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _setup_results(self) -> None:
        self._results_key: tuple[object, ...] | None = None
        self._all_result_rows: list[ResultRow] = []
        self._results_data_signature: tuple[object, ...] | None = None
        self._results_meta_signature: tuple[object, ...] | None = None
        self._latest_result_health = None
        self._latest_result_comparison = None
        self._filtered_result_rows: list[ResultRow] = []
        self._result_page_index = 0
        self._results_search_dirty = False
        self.results_summary = text(t("No results yet."), 13)
        self.results_search = ft.TextField(
            label=t("Search domain"),
            dense=True,
            on_change=self._on_results_search_change,
            on_submit=lambda _e: self.refresh_results(),
        )
        self.verdict_filter = ft.Dropdown(
            label=t("Verdict"),
            value="all",
            dense=True,
            options=[
                ft.DropdownOption(key="all", text=t("All verdicts")),
                ft.DropdownOption(key=Verdict.OPENED.value, text=t("Opened (leaks)")),
                ft.DropdownOption(key=Verdict.BLOCKED.value, text=t("Blocked")),
                ft.DropdownOption(key=Verdict.UNREACHABLE.value, text=t("Unreachable")),
            ],
            on_select=lambda _e: self.refresh_results(),
        )
        self.leaks_only_switch = ft.Switch(
            label=t("Leaks only"), value=False, on_change=lambda _e: self.refresh_results()
        )
        self.results_table = ft.DataTable(
            columns=[
                ft.DataColumn(label=text(t("Domain"), 12, TEXT)),
                ft.DataColumn(label=text(t("Verdict"), 12, TEXT)),
                ft.DataColumn(label=text(t("Why"), 12, TEXT)),
                ft.DataColumn(label=text(t("Confidence"), 12, TEXT)),
                ft.DataColumn(label=text(t("Review"), 12, TEXT)),
                ft.DataColumn(label=text(t("Source"), 12, TEXT)),
            ],
            rows=[],
            column_spacing=18,
        )
        self.results_scroll = ft.Row([self.results_table], scroll=ft.ScrollMode.AUTO)
        self.results_mobile_list = ft.Column(spacing=8, visible=False)
        self.results_page_label = text("", 12, MUTED)
        self.export_status = text("", 12, MUTED, selectable=True)
        self.results_pager = ft.Row(
            [
                button(t("Previous"), ft.Icons.CHEVRON_LEFT, self._previous_results_page),
                self.results_page_label,
                button(t("Next"), ft.Icons.CHEVRON_RIGHT, self._next_results_page),
            ],
            alignment=ft.MainAxisAlignment.CENTER,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            spacing=12,
            visible=False,
        )
        self.results_empty = ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.SEARCH_OFF, size=30, color=MUTED),
                    text(t("No matching results."), 16, TEXT, weight=ft.FontWeight.W_600),
                    text(t("Clear filters or run a scan to see results here."), 12, MUTED),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=8,
            ),
            padding=30,
            alignment=ft.Alignment.CENTER,
            visible=False,
        )
        self.comparison_text = text(t("Comparison appears after two runs."), 12)

    def _results_view(self) -> ft.Control:
        return ft.Column(
            [
                hero(
                    t("Evidence from your scans"),
                    t("Review each verdict alongside its reason, confidence and network health."),
                    icon=ft.Icons.ANALYTICS_OUTLINED,
                ),
                card(t("Summary"), self.results_summary),
                card(
                    t("Filters and export"),
                    ft.Column(
                        [
                            ft.ResponsiveRow(
                                [
                                    ft.Container(
                                        content=self.verdict_filter,
                                        col={"xs": 12, "sm": 6, "md": 3},
                                    ),
                                    ft.Container(
                                        content=self.leaks_only_switch,
                                        col={"xs": 12, "sm": 6, "md": 3},
                                    ),
                                    ft.Container(
                                        content=self.results_search,
                                        col={"xs": 12, "sm": 8, "md": 4},
                                    ),
                                    ft.Container(
                                        content=button(
                                            t("Apply"),
                                            ft.Icons.FILTER_ALT,
                                            self._wrap(self.refresh_results),
                                        ),
                                        col={"xs": 12, "sm": 4, "md": 2},
                                    ),
                                ],
                                run_spacing=8,
                            ),
                            ft.Row(
                                [
                                    button(
                                        t("Markdown"),
                                        ft.Icons.DESCRIPTION,
                                        self._wrap(
                                            lambda: self._export(
                                                lambda: actions.export_report("markdown")
                                            )
                                        ),
                                    ),
                                    button(
                                        t("CSV"),
                                        ft.Icons.TABLE_VIEW,
                                        self._wrap(
                                            lambda: self._export(
                                                lambda: actions.export_report("csv")
                                            )
                                        ),
                                    ),
                                    button(
                                        t("JSON"),
                                        ft.Icons.DATA_OBJECT,
                                        self._wrap(
                                            lambda: self._export(
                                                lambda: actions.export_report("json")
                                            )
                                        ),
                                    ),
                                    button(
                                        t("Domain list (txt)"),
                                        ft.Icons.LIST_ALT,
                                        self._wrap(lambda: self._export(actions.export_domain_list)),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                            self.export_status,
                        ],
                        spacing=10,
                    ),
                ),
                card(t("Comparison with the previous run"), self.comparison_text),
                card(
                    t("Results (domains are never links)"),
                    ft.Column(
                        [
                            self.results_empty,
                            self.results_mobile_list,
                            self.results_pager,
                            self.results_scroll,
                        ],
                        expand=True,
                    ),
                    expand=True,
                ),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
            expand=True,
        )

    def _on_results_search_change(self, _event: ft.ControlEvent) -> None:
        self._results_search_dirty = True

    def refresh_results(self, *, update_page: bool = False) -> actions.ActionResult:
        self._results_search_dirty = False
        summary = actions.latest_run_summary()
        signature: tuple[object, ...] | None = (
            (
                summary.run_id,
                summary.total,
                summary.blocked,
                summary.unreachable,
                summary.opened,
                summary.needs_review,
                summary.status.value,
            )
            if summary is not None
            else None
        )
        if signature != self._results_data_signature:
            self._all_result_rows = actions.load_results()
            self._results_data_signature = signature
        rows = self._all_result_rows
        if bool(self.leaks_only_switch.value):
            rows = [row for row in rows if row.verdict is Verdict.OPENED]
        query = (self.results_search.value or "").strip().casefold()
        if query:
            rows = [row for row in rows if query in row.domain.casefold()]
        verdict_value = str(self.verdict_filter.value or "all")
        if verdict_value != "all":
            rows = [row for row in rows if row.verdict.value == verdict_value]
        self._filtered_result_rows = rows
        self._result_page_index = 0
        self.results_empty.visible = not rows
        compact = bool(getattr(self, "_compact_layout", False))
        self.results_scroll.visible = bool(rows) and not compact
        self.results_mobile_list.visible = bool(rows) and compact
        self.results_pager.visible = bool(rows) and compact and len(rows) > self.RESULTS_PAGE_SIZE
        self._render_result_page()
        self.results_table.rows = (
            [self._desktop_result_row(row) for row in rows] if not compact else []
        )
        if signature != self._results_meta_signature:
            self._latest_result_health = (
                actions.latest_run_health(summary.run_id) if summary is not None else None
            )
            self._latest_result_comparison = actions.load_comparison()
            self._results_meta_signature = signature
        if summary is None:
            self.results_summary.value = t("No run stored yet. Start a scan first.")
        else:
            self.results_summary.value = t(
                "Run {run_id} — {status}: {checked} checked, {opened} opened, "
                "{blocked} blocked, {unreachable} unreachable, "
                "{needs_review} need review ({percent}% blocked).",
                run_id=summary.run_id[:8],
                status=summary.status.value,
                checked=summary.total,
                opened=summary.opened,
                blocked=summary.blocked,
                unreachable=summary.unreachable,
                needs_review=summary.needs_review,
                percent=summary.blocked_percent,
            )
            health = self._latest_result_health
            if health is not None:
                self.results_summary.value += "\n" + t(
                    "Interference signal: {percent}% ({level})",
                    percent=health.risk_percent,
                    level=health.risk_level,
                )
                self.results_summary.value += "\n" + t(
                    "This is a heuristic signal, not a measured probability. "
                    "Neutral website checks help separate network trouble from "
                    "individual site failures."
                )
                if health.first_failure_after:
                    self.results_summary.value += " " + t(
                        "First neutral control failure after: {domain}",
                        domain=health.first_failure_after,
                    )
                if health.recheck_domains:
                    self.results_summary.value += " " + t(
                        "Recheck these domains after the scan: {domains}",
                        domains=", ".join(health.recheck_domains[:6]),
                    )
        comparison = self._latest_result_comparison
        if comparison is None or comparison.previous_run_id is None:
            self.comparison_text.value = t("Comparison appears after two runs.")
        else:
            self.comparison_text.value = t(
                "Compared with {run_id}: {newly_leaking} newly leaking, "
                "{newly_blocked} newly blocked, {still_leaking} still leaking, "
                "{resolved} resolved.",
                run_id=comparison.previous_run_id[:8],
                newly_leaking=len(comparison.newly_leaking),
                newly_blocked=len(comparison.newly_blocked),
                still_leaking=len(comparison.still_leaking),
                resolved=len(comparison.resolved),
            )
        self._results_key = (
            (
                summary.run_id,
                summary.total,
                summary.blocked,
                summary.unreachable,
                summary.opened,
                summary.needs_review,
                summary.status.value,
            )
            if summary is not None
            else None
        )
        if update_page:
            try:
                self.page.update()
            except Exception:
                pass
        return actions.ActionResult(
            t("Results refreshed"), t("{count} row(s) shown.", count=len(rows))
        )

    def refresh_results_layout(self, compact: bool) -> None:
        """Use readable result cards on phones and the full table on wide screens."""
        self._compact_layout = compact
        self.results_scroll.visible = bool(self.results_table.rows) and not compact
        self.results_mobile_list.visible = bool(self._filtered_result_rows) and compact
        self.results_pager.visible = (
            compact and len(self._filtered_result_rows) > self.RESULTS_PAGE_SIZE
        )
        if compact:
            self.results_table.rows = []
        else:
            self.results_table.rows = [
                self._desktop_result_row(row) for row in self._filtered_result_rows
            ]
        self._render_result_page()

    def _render_result_page(self) -> None:
        total = len(self._filtered_result_rows)
        start = self._result_page_index * self.RESULTS_PAGE_SIZE
        page_rows = self._filtered_result_rows[start : start + self.RESULTS_PAGE_SIZE]
        self.results_mobile_list.controls = [self._mobile_result_card(row) for row in page_rows]
        end = min(start + len(page_rows), total)
        self.results_page_label.value = t(
            "Showing {start}-{end} of {total}",
            start=start + 1 if total else 0,
            end=end,
            total=total,
        )

    def _previous_results_page(self, _event: object = None) -> None:
        if self._result_page_index > 0:
            self._result_page_index -= 1
            self._render_result_page()
            apply_page_theme(
                self.page,
                actions.runtime().settings.config.app.theme_mode,
                update=False,
            )
            self.page.update()

    def _next_results_page(self, _event: object = None) -> None:
        last_page = max(0, (len(self._filtered_result_rows) - 1) // self.RESULTS_PAGE_SIZE)
        if self._result_page_index < last_page:
            self._result_page_index += 1
            self._render_result_page()
            apply_page_theme(
                self.page,
                actions.runtime().settings.config.app.theme_mode,
                update=False,
            )
            self.page.update()

    def _export(self, action: Callable[[], actions.ActionResult]) -> actions.ActionResult:
        result = action()
        self.export_status.value = (
            f"{result.title}: {result.detail}" if result.detail else result.title
        )
        return result

    @classmethod
    def _desktop_result_row(cls, row: ResultRow) -> ft.DataRow:
        return ft.DataRow(
            cells=[
                ft.DataCell(text(row.domain, 12, TEXT)),
                ft.DataCell(
                    text(
                        cls._result_verdict_label(row),
                        12,
                        cls._verdict_color(row.verdict),
                    )
                ),
                ft.DataCell(text(row.reason_label, 12)),
                ft.DataCell(text(row.confidence.label, 12)),
                ft.DataCell(
                    text(
                        t("yes") if row.needs_review else t("no"),
                        12,
                        WARN if row.needs_review else MUTED,
                    )
                ),
                ft.DataCell(text(", ".join(row.sources) or "—", 12)),
            ]
        )

    @staticmethod
    def _mobile_result_card(row: ResultRow) -> ft.Control:
        verdict_color = ResultsScreen._verdict_color(row.verdict)
        return ft.Container(
            content=ft.Column(
                [
                    text(
                        row.domain,
                        14,
                        TEXT,
                        weight=ft.FontWeight.W_600,
                        selectable=True,
                    ),
                    ft.Row(
                        [
                            text(ResultsScreen._result_verdict_label(row), 12, verdict_color),
                            text(row.confidence.label, 12, MUTED),
                            text(
                                f"{t('Review')}: {t('yes') if row.needs_review else t('no')}",
                                12,
                                WARN if row.needs_review else MUTED,
                            ),
                        ],
                        wrap=True,
                        spacing=8,
                    ),
                    text(
                        t("{label}: {value}", label=t("Why"), value=row.reason_label),
                        12,
                        MUTED,
                    ),
                    text(
                        t(
                            "{label}: {value}",
                            label=t("Source"),
                            value=", ".join(row.sources) or "—",
                        ),
                        12,
                        MUTED,
                    ),
                ],
                spacing=6,
            ),
            padding=12,
            bgcolor=SURFACE,
            border=ft.Border.all(1, BORDER),
            border_radius=10,
        )

    @staticmethod
    def _verdict_color(verdict: Verdict) -> str:
        if verdict is Verdict.OPENED:
            return BAD
        if verdict is Verdict.BLOCKED:
            return OK
        return WARN

    @staticmethod
    def _result_verdict_label(row: ResultRow) -> str:
        if row.verdict is Verdict.UNREACHABLE and row.needs_review:
            return t("Could not verify")
        return row.verdict.label
