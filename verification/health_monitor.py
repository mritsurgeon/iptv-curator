"""Adaptive scheduled health monitor with survival states, backoff & event priority."""

import time
from typing import Any, Dict, List, Optional

from database.database import DatabaseManager
from verification.stream_validator import StreamValidator


class HealthMonitor:
    """
    Rechecks due stream endpoints within a time budget, prioritizes endpoints with
    upcoming live events, applies active -> degraded -> inactive survival transitions,
    and automatically restores recovered endpoints.
    """

    def __init__(self, db: DatabaseManager, validator: StreamValidator):
        self.db = db
        self.validator = validator

    def run_health_check(
        self,
        limit: int = 500,
        only_due: bool = True,
        time_budget_sec: float = 480.0,
        priority_channel_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        endpoints = self.db.get_endpoints_for_health_check(
            only_due=only_due,
            priority_channel_ids=priority_channel_ids,
        )[:limit]

        summary: Dict[str, Any] = {
            "checked": 0,
            "deferred": 0,
            "working": 0,
            "degraded": 0,
            "failed": 0,
            "auth_required": 0,
            "restored": [],
            "deactivated": [],
        }
        if not endpoints:
            return summary

        t_start = time.monotonic()
        batch_size = max(1, min(self.validator.max_workers, 25))

        for i in range(0, len(endpoints), batch_size):
            if (time.monotonic() - t_start) >= time_budget_sec:
                summary["deferred"] = len(endpoints) - i
                break

            chunk = endpoints[i : i + batch_size]
            url_to_ep = {ep["url"]: ep for ep in chunk}
            results = self.validator.verify_batch(list(url_to_ep.keys()))

            for url, res in results.items():
                ep = url_to_ep[url]
                prev_status = ep.get("status", "pending")
                new_status = self.db.record_verification(ep["channel_id"], res)
                summary["checked"] += 1

                if new_status == "active":
                    summary["working"] += 1
                    if prev_status in ("inactive", "auth_required", "degraded"):
                        summary["restored"].append({"channel_id": ep["channel_id"], "url": url})
                elif new_status == "degraded":
                    summary["degraded"] += 1
                elif new_status == "auth_required":
                    summary["auth_required"] += 1
                    if prev_status in ("active", "degraded"):
                        summary["deactivated"].append(
                            {"channel_id": ep["channel_id"], "url": url, "reason": res.failure_reason}
                        )
                else:
                    summary["failed"] += 1
                    if prev_status in ("active", "degraded"):
                        summary["deactivated"].append(
                            {"channel_id": ep["channel_id"], "url": url, "reason": res.failure_reason}
                        )

        return summary
