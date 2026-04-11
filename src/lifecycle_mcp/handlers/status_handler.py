#!/usr/bin/env python3
"""
Status Handler for MCP Lifecycle Management Server (v2)

Provides project-scoped health dashboards, structured metrics, and
lifecycle change diffs.

Tools:
  - get_project_status: human-readable project health dashboard
  - get_project_metrics: structured JSON metrics for programmatic use
  - diff_project: lifecycle_events in a time window
"""

import json
from typing import Any

from mcp.types import TextContent

from .base_handler import BaseHandler


class StatusHandler(BaseHandler):
    """Handler for project status and metrics MCP tools (v2 schema)"""

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return status tool definitions"""
        return [
            {
                "name": "get_project_status",
                "description": "Get overall project health metrics",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "include_blocked": {"type": "boolean", "default": False},
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "get_project_metrics",
                "description": "Get structured project metrics for programmatic use",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "diff_project",
                "description": "Get entities that changed status in a time window",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "from_timestamp": {"type": "string", "description": "ISO timestamp"},
                        "to_timestamp": {"type": "string", "description": "ISO timestamp"},
                    },
                    "required": ["project_id", "from_timestamp", "to_timestamp"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Route tool calls to appropriate handler methods"""
        handlers = {
            "get_project_status": self._get_project_status,
            "get_project_metrics": self._get_project_metrics,
            "diff_project": self._diff_project,
        }
        handler_fn = handlers.get(tool_name)
        if handler_fn is None:
            return self._create_error_response(f"Unknown tool: {tool_name}")
        try:
            return await handler_fn(**arguments)
        except Exception as e:
            return self._create_error_response(f"Error handling {tool_name}", e)

    # ------------------------------------------------------------------
    # get_project_status
    # ------------------------------------------------------------------

    async def _get_project_status(self, project_id: str, include_blocked: bool = False, **_kw) -> list[TextContent]:
        """Get project health dashboard scoped to a single project."""

        # Validate project exists
        err = await self._validate_project_exists(project_id)
        if err:
            return self._create_error_response(err)

        # Fetch project name from project_summary view
        summary = await self.db.execute_query(
            "SELECT name, status FROM project_summary WHERE id = ?",
            [project_id],
            fetch_one=True,
            row_factory=True,
        )
        project_name = summary["name"] if summary else project_id
        project_status = summary["status"] if summary else "unknown"

        # ── Requirement breakdown ────────────────────────────────────
        req_stats = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM requirements "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )
        total_reqs = sum(r["count"] for r in req_stats) if req_stats else 0
        req_parts = ", ".join(f"{r['count']} {r['status']}" for r in req_stats) if req_stats else ""

        # ── Task breakdown ───────────────────────────────────────────
        task_stats = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )
        total_tasks = sum(t["count"] for t in task_stats) if task_stats else 0
        completed_tasks = next((t["count"] for t in task_stats if t["status"] == "Complete"), 0) if task_stats else 0
        completion_pct = round(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        task_parts = ", ".join(f"{t['count']} {t['status']}" for t in task_stats) if task_stats else ""

        # ── ADR breakdown ────────────────────────────────────────────
        adr_stats = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM architecture "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )
        total_adrs = sum(a["count"] for a in adr_stats) if adr_stats else 0
        adr_parts = ", ".join(f"{a['count']} {a['status']}" for a in adr_stats) if adr_stats else ""

        # ── Build report ─────────────────────────────────────────────
        lines = [
            f"[SUCCESS] Project {project_id}: {project_name} [{project_status}]",
            "",
            f"Requirements: {total_reqs} total" + (f" ({req_parts})" if req_parts else ""),
            f"Tasks: {total_tasks} total" + (f" ({task_parts})" if task_parts else "") + (f" -- {completion_pct}% complete" if total_tasks > 0 else ""),
            f"Architecture: {total_adrs} total" + (f" ({adr_parts})" if adr_parts else ""),
        ]

        # ── Blocked tasks ────────────────────────────────────────────
        if include_blocked:
            blocked = await self.db.execute_query(
                "SELECT id, title, blocked_by_id FROM blocked_tasks WHERE project_id = ?",
                [project_id],
                fetch_all=True,
                row_factory=True,
            )
            if blocked:
                lines.append("")
                lines.append("Blocked Tasks:")
                for b in blocked:
                    lines.append(f"- {b['id']}: {b['title']} (blocked by {b['blocked_by_id']})")

        return [TextContent(type="text", text="\n".join(lines))]

    # ------------------------------------------------------------------
    # get_project_metrics
    # ------------------------------------------------------------------

    async def _get_project_metrics(self, project_id: str, **_kw) -> list[TextContent]:
        """Return structured JSON metrics scoped to a project."""

        # Validate project exists
        err = await self._validate_project_exists(project_id)
        if err:
            return self._create_error_response(err)

        # ── Requirements ─────────────────────────────────────────────
        req_by_status = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM requirements "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )
        req_by_priority = await self.db.execute_query(
            "SELECT priority, COUNT(*) as count FROM requirements "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY priority",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )

        # ── Tasks ────────────────────────────────────────────────────
        task_by_status = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )
        task_by_priority = await self.db.execute_query(
            "SELECT priority, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY priority",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )
        task_by_assignee = await self.db.execute_query(
            "SELECT COALESCE(assignee, 'Unassigned') as assignee, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY assignee",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )

        # ── Architecture ─────────────────────────────────────────────
        adr_by_status = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM architecture "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )

        # ── Blocked count ────────────────────────────────────────────
        blocked_row = await self.db.execute_query(
            "SELECT COUNT(*) as cnt FROM blocked_tasks WHERE project_id = ?",
            [project_id],
            fetch_one=True,
            row_factory=True,
        )
        blocked_count = blocked_row["cnt"] if blocked_row else 0

        # ── Assemble metrics ─────────────────────────────────────────
        total_tasks = sum(t["count"] for t in task_by_status) if task_by_status else 0
        completed_tasks = next((t["count"] for t in task_by_status if t["status"] == "Complete"), 0) if task_by_status else 0
        completion_pct = round(completed_tasks / total_tasks * 100, 1) if total_tasks > 0 else 0

        metrics = {
            "project_id": project_id,
            "requirements": {
                "total": sum(r["count"] for r in req_by_status) if req_by_status else 0,
                "by_status": {r["status"]: r["count"] for r in req_by_status} if req_by_status else {},
                "by_priority": {r["priority"]: r["count"] for r in req_by_priority} if req_by_priority else {},
            },
            "tasks": {
                "total": total_tasks,
                "by_status": {t["status"]: t["count"] for t in task_by_status} if task_by_status else {},
                "by_priority": {t["priority"]: t["count"] for t in task_by_priority} if task_by_priority else {},
                "by_assignee": {t["assignee"]: t["count"] for t in task_by_assignee} if task_by_assignee else {},
                "completion_pct": completion_pct,
            },
            "architecture": {
                "total": sum(a["count"] for a in adr_by_status) if adr_by_status else 0,
                "by_status": {a["status"]: a["count"] for a in adr_by_status} if adr_by_status else {},
            },
            "blocked_count": blocked_count,
        }

        return [TextContent(type="text", text=json.dumps(metrics))]

    # ------------------------------------------------------------------
    # diff_project
    # ------------------------------------------------------------------

    async def _diff_project(
        self, project_id: str, from_timestamp: str, to_timestamp: str, **_kw
    ) -> list[TextContent]:
        """Return lifecycle events (status changes) in a time window for a project."""

        # Validate project exists
        err = await self._validate_project_exists(project_id)
        if err:
            return self._create_error_response(err)

        events = await self.db.execute_query(
            "SELECT entity_type, entity_id, from_value, to_value, occurred_at "
            "FROM lifecycle_events "
            "WHERE project_id = ? AND event_type = 'status_change' "
            "AND occurred_at BETWEEN ? AND ? "
            "ORDER BY occurred_at",
            [project_id, from_timestamp, to_timestamp],
            fetch_all=True,
            row_factory=True,
        )

        changes = []
        req_ids = set()
        task_ids = set()
        adr_ids = set()

        for ev in (events or []):
            changes.append({
                "entity_type": ev["entity_type"],
                "entity_id": ev["entity_id"],
                "from_status": ev["from_value"],
                "to_status": ev["to_value"],
                "occurred_at": ev["occurred_at"],
            })
            if ev["entity_type"] == "requirement":
                req_ids.add(ev["entity_id"])
            elif ev["entity_type"] == "task":
                task_ids.add(ev["entity_id"])
            elif ev["entity_type"] == "architecture":
                adr_ids.add(ev["entity_id"])

        result = {
            "changes": changes,
            "summary": {
                "requirements_changed": len(req_ids),
                "tasks_changed": len(task_ids),
                "adrs_changed": len(adr_ids),
            },
        }

        return [TextContent(type="text", text=json.dumps(result))]
