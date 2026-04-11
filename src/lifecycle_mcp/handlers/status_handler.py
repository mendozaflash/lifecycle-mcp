#!/usr/bin/env python3
"""
Status Handler for MCP Lifecycle Management Server (v2)

Provides project-scoped lifecycle change diffs.

Tools:
  - diff_project: lifecycle_events in a time window
"""

import json
from typing import Any

from mcp.types import TextContent

from .base_handler import BaseHandler


class StatusHandler(BaseHandler):
    """Handler for project status MCP tools (v2 schema)"""

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return status tool definitions"""
        return [
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
