# SPDX-License-Identifier: AGPL-3.0-or-later
"""Legacy robots.txt parser kept for compatibility; the app does not use it.

Portal refreshes never request or enforce robots.txt. This parser remains for
external callers that need to inspect previously saved robots text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass(slots=True)
class RobotsPolicy:
    allowed: bool = True
    crawl_delay: float | None = None
    source: str = ""
    note: str = ""
    rules: list[tuple[bool, str]] = field(default_factory=list)

    def can_fetch(self, url: str) -> bool:
        if not self.allowed:
            return False
        path = urlsplit(url).path or "/"
        query = urlsplit(url).query
        target = path + (f"?{query}" if query else "")
        best: tuple[int, bool] | None = None
        for allow, pattern in self.rules:
            if not pattern:
                continue
            matched = (
                target.startswith("/") if pattern == "/" else target.startswith(pattern)
            )
            if not matched:
                continue
            length = len(pattern)
            if best is None or length > best[0] or (length == best[0] and allow and not best[1]):
                best = (length, allow)
        return True if best is None else best[1]


def parse_robots(text: str, user_agent_token: str = "blockprobe") -> RobotsPolicy:
    policy = RobotsPolicy(allowed=True, source="robots.txt")
    groups: list[dict[str, object]] = []
    current_agents: list[str] = []
    current_rules: list[tuple[bool, str]] = []
    current_delay: float | None = None

    def flush() -> None:
        nonlocal current_agents, current_rules, current_delay
        if current_agents:
            groups.append(
                {
                    "agents": [agent.lower() for agent in current_agents],
                    "rules": list(current_rules),
                    "delay": current_delay,
                }
            )
        current_agents = []
        current_rules = []
        current_delay = None

    for raw_line in (text or "").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip()
        if key == "user-agent":
            if current_agents and (current_rules or current_delay is not None):
                flush()
            current_agents.append(value)
        elif key in {"allow", "disallow"}:
            if not current_agents:
                current_agents = ["*"]
            current_rules.append((key == "allow", value))
        elif key == "crawl-delay":
            try:
                current_delay = max(0.0, float(value))
            except ValueError:
                current_delay = None
    flush()

    token = user_agent_token.lower()
    chosen: dict[str, object] | None = None
    best_agent_length = -1
    for group in groups:
        agents = [str(agent) for agent in group.get("agents", [])]
        for agent in agents:
            if agent == "*" and chosen is None:
                chosen = group
            elif (
                (agent == token or token.startswith(agent.rstrip("*")))
                and len(agent) > best_agent_length
            ):
                chosen = group
                best_agent_length = len(agent)
    if chosen is None:
        policy.note = "no matching user-agent group; allowing access"
        return policy
    policy.rules = list(chosen.get("rules", []))  # type: ignore[arg-type]
    delay = chosen.get("delay")
    if isinstance(delay, float):
        policy.crawl_delay = delay
    return policy
