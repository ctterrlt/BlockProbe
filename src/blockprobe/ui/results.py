# SPDX-License-Identifier: AGPL-3.0-or-later
"""Results table, filters, comparison and exports (FR-8, FR-10)."""

from __future__ import annotations

import flet as ft

from blockprobe.core.enums import Verdict
from blockprobe.core.models import ResultRow
from blockprobe.i18n import t
from blockprobe.ui import actions
from blockprobe.ui.components import MUTED, OK, TEXT, WARN, button, card, hero, text


class ResultsScreen:
    """Mixin with the results screen and its filters."""

    page: ft.Page
    results_summary: ft.Text
    results_search: ft.TextField
    verdict_filter: ft.Dropdown
    leaks_only_switch: ft.Switch
    results_table: ft.DataTable
    results_scroll: ft.Row
    results_empty: ft.Container
    comparison_text: ft.Text

    def _wrap(self, action):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    def _setup_results(self) -> None:
        self._results_key: tuple[object, ...] | None = None
        self.results_summary = text(t("No results yet."), 13)
        self.results_search = ft.TextField(
            label=t("Search domain"),
            dense=True,
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
                                            lambda: actions.export_report("markdown")
                                        ),
                                    ),
                                    button(
                                        t("CSV"),
                                        ft.Icons.TABLE_VIEW,
                                        self._wrap(lambda: actions.export_report("csv")),
                                    ),
                                    button(
                                        t("JSON"),
                                        ft.Icons.DATA_OBJECT,
                                        self._wrap(lambda: actions.export_report("json")),
                                    ),
                                    button(
                                        t("Domain list (txt)"),
                                        ft.Icons.LIST_ALT,
                                        self._wrap(actions.export_domain_list),
                                    ),
                                ],
                                wrap=True,
                                spacing=12,
                            ),
                        ],
                        spacing=10,
                    ),
                ),
                card(t("Comparison with the previous run"), self.comparison_text),
                card(
                    t("Results (domains are never links)"),
                    ft.Column([self.results_empty, self.results_scroll], expand=True),
                    expand=True,
                ),
            ],
            spacing=16,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        )

    def refresh_results(self) -> actions.ActionResult:
        rows: list[ResultRow] = actions.load_results(
            leaks_only=bool(self.leaks_only_switch.value),
            search=self.results_search.value or "",
        )
        verdict_value = str(self.verdict_filter.value or "all")
        if verdict_value != "all":
            rows = [row for row in rows if row.verdict.value == verdict_value]
        self.results_empty.visible = not rows
        self.results_scroll.visible = bool(rows)
        self.results_table.rows = [
            ft.DataRow(
                cells=[
                    ft.DataCell(text(row.domain, 12, TEXT)),
                    ft.DataCell(
                        text(
                            row.verdict.label,
                            12,
                            OK if row.verdict is Verdict.OPENED else MUTED,
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
            for row in rows
        ]
        summary = actions.latest_run_summary()
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
            health = actions.latest_run_health(summary.run_id)
            if health is not None:
                self.results_summary.value += "\n" + t(
                    "Interference risk: {percent}% ({level})",
                    percent=health.risk_percent,
                    level=health.risk_level,
                )
                self.results_summary.value += "\n" + t(
                    "Risk is a run-level estimate. Detected VPN, proxy or DNS "
                    "filtering adds risk; unknown or disabled factors add none."
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
        comparison = actions.load_comparison()
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
        self._results_key = (summary.run_id if summary else None, len(rows))
        try:
            self.page.update()
        except Exception:
            pass
        return actions.ActionResult(
            t("Results refreshed"), t("{count} row(s) shown.", count=len(rows))
        )
