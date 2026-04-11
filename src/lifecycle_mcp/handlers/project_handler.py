#!/usr/bin/env python3
"""
Project Handler for MCP Lifecycle Management Server
Handles project CRUD, archiving, listing, and detail operations
"""

import json
from typing import Any

from mcp.types import TextContent

from .base_handler import BaseHandler


class ProjectHandler(BaseHandler):
    """Handler for project management MCP tools"""

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return project tool definitions"""
        return [
            {
                "name": "create_project",
                "description": "Create a new project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Project name"},
                        "description": {"type": "string", "description": "Project description"},
                        "tech_stack": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Technology stack",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Project constraints",
                        },
                    },
                    "required": ["name"],
                },
            },
            {
                "name": "update_project",
                "description": "Update an existing project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "name": {"type": "string", "description": "New project name"},
                        "description": {"type": "string", "description": "New project description"},
                        "status": {
                            "type": "string",
                            "enum": ["active", "archived"],
                            "description": "Project status",
                        },
                        "tech_stack": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Technology stack",
                        },
                        "constraints": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Project constraints",
                        },
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "archive_project",
                "description": "Archive a project and all its children (requirements, tasks, architecture)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "list_projects",
                "description": "List projects with id, name, and status",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "include_archived": {
                            "type": "boolean",
                            "description": "Include archived projects (default: false)",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["active", "archived"],
                            "description": "Filter by status",
                        },
                    },
                },
            },
            {
                "name": "get_project_details",
                "description": "Get project details at varying depth: summary (metadata + totals), status (+ per-status breakdowns, completion %), or metrics (+ priority/assignee/effort breakdowns as JSON)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "detail_level": {
                            "type": "string",
                            "enum": ["summary", "status", "metrics"],
                            "description": "Level of detail (default: summary)",
                        },
                    },
                    "required": ["project_id"],
                },
            },
        ]

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Route tool calls to appropriate handler methods"""
        handlers = {
            "create_project": self._create_project,
            "update_project": self._update_project,
            "archive_project": self._archive_project,
            "list_projects": self._list_projects,
            "get_project_details": self._get_project_details,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._create_error_response(f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return self._create_error_response(f"Error handling {tool_name}", e)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    async def _create_project(self, params: dict[str, Any]) -> list[TextContent]:
        """Create a new project."""
        error = self._validate_required_params(params, ["name"])
        if error:
            return self._create_error_response(error)

        project_id, _ = await self.db.generate_id("project")

        data: dict[str, Any] = {
            "id": project_id,
            "name": params["name"],
        }
        if "description" in params and params["description"] is not None:
            data["description"] = params["description"]
        if "tech_stack" in params and params["tech_stack"] is not None:
            data["tech_stack"] = self._safe_json_dumps(params["tech_stack"])
        if "constraints" in params and params["constraints"] is not None:
            data["constraints"] = self._safe_json_dumps(params["constraints"])

        await self.db.insert_record("projects", data)
        await self._log_operation("project", project_id, "created", project_id=project_id)

        return self._create_above_fold_response(
            "SUCCESS",
            self._format_status_summary("Project", project_id, "active", params["name"]),
        )

    async def _update_project(self, params: dict[str, Any]) -> list[TextContent]:
        """Update an existing project."""
        error = self._validate_required_params(params, ["project_id"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]

        # Validate exists
        error = await self._validate_entity_exists("project", project_id)
        if error:
            return self._create_error_response(error)

        # Validate not archived
        error = await self._validate_not_archived("project", project_id)
        if error:
            return self._create_error_response(error)

        # Build update data
        updatable_fields = ["name", "description", "status"]
        json_fields = ["tech_stack", "constraints"]

        data: dict[str, Any] = {}
        for field in updatable_fields:
            if field in params and params[field] is not None:
                data[field] = params[field]
        for field in json_fields:
            if field in params and params[field] is not None:
                data[field] = self._safe_json_dumps(params[field])

        if not data:
            return self._create_error_response("No fields to update")

        await self.db.update_record("projects", data, "id = ?", [project_id])
        await self._log_operation("project", project_id, "updated", project_id=project_id)

        return self._create_above_fold_response(
            "SUCCESS",
            f"Project {project_id} updated",
            f"Updated fields: {', '.join(data.keys())}",
        )

    async def _archive_project(self, params: dict[str, Any]) -> list[TextContent]:
        """Archive a project and cascade to all children."""
        error = self._validate_required_params(params, ["project_id"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]

        # Validate exists
        error = await self._validate_entity_exists("project", project_id)
        if error:
            return self._create_error_response(error)

        # Archive the project itself
        await self.db.execute_query(
            "UPDATE projects SET is_archived = 1, archived_at = datetime('now'), "
            "status = 'archived' WHERE id = ?",
            [project_id],
        )

        # Cascade: archive all children
        for table in ["requirements", "tasks", "architecture"]:
            await self.db.execute_query(
                f"UPDATE {table} SET is_archived = 1, archived_at = datetime('now') "
                f"WHERE project_id = ? AND is_archived = 0",
                [project_id],
            )

        await self._log_operation("project", project_id, "archived", project_id=project_id)

        return self._create_above_fold_response(
            "SUCCESS",
            f"Project {project_id} archived",
            "All requirements, tasks, and architecture decisions in this project have been archived.",
        )

    async def _list_projects(self, params: dict[str, Any]) -> list[TextContent]:
        """List projects returning only id, name, status per project."""
        include_archived = params.get("include_archived", False)
        status_filter = params.get("status")

        conditions: list[str] = []
        query_params: list[Any] = []

        if not include_archived:
            conditions.append("is_archived = 0")

        if status_filter:
            conditions.append("status = ?")
            query_params.append(status_filter)

        where_clause = " AND ".join(conditions) if conditions else ""

        rows = await self.db.get_records(
            "projects",
            where_clause=where_clause,
            where_params=query_params,
            order_by="created_at DESC",
        )

        count = len(rows)
        filter_desc = status_filter or ("all" if include_archived else "active")

        if count == 0:
            return self._create_above_fold_response(
                "INFO",
                self._format_count_summary("project", 0, filter_desc),
            )

        lines = []
        for row in rows:
            lines.append(f"  {row['id']}: {row['name']} [{row['status']}]")

        details = "\n".join(lines)

        return self._create_above_fold_response(
            "SUCCESS",
            self._format_count_summary("project", count, filter_desc),
            details=details,
        )

    async def _get_project_details(self, params: dict[str, Any]) -> list[TextContent]:
        """Get project details at varying depth via detail_level parameter.

        detail_level values:
          - summary (default): metadata + total counts
          - status: summary + per-status breakdowns, completion %
          - metrics: status + priority/assignee/effort breakdowns as JSON
        """
        error = self._validate_required_params(params, ["project_id"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        detail_level = params.get("detail_level", "summary")

        # Validate exists
        error = await self._validate_entity_exists("project", project_id)
        if error:
            return self._create_error_response(error)

        if detail_level == "metrics":
            return await self._get_project_details_metrics(project_id)
        elif detail_level == "status":
            return await self._get_project_details_status(project_id)
        else:
            return await self._get_project_details_summary(project_id)

    # ------------------------------------------------------------------
    # Detail level implementations
    # ------------------------------------------------------------------

    async def _get_project_details_summary(self, project_id: str) -> list[TextContent]:
        """Summary level: metadata + total counts."""
        rows = await self.db.get_records(
            "projects", where_clause="id = ?", where_params=[project_id]
        )
        project = rows[0]

        req_count = await self._count_entities("requirements", project_id)
        task_count = await self._count_entities("tasks", project_id)
        tasks_complete = await self._count_entities("tasks", project_id, status="Validated")
        adr_count = await self._count_entities("architecture", project_id)

        status = project["status"]
        name = project["name"]
        desc = project["description"] or "No description"
        tech_stack = self._safe_json_loads(project["tech_stack"])
        constraints = self._safe_json_loads(project["constraints"])

        key_info = self._format_status_summary("Project", project_id, status, name)
        action_info = (
            f"Requirements: {req_count} | Tasks: {task_count} ({tasks_complete} complete) | ADRs: {adr_count}"
        )

        detail_lines = [f"Description: {desc}"]
        if tech_stack:
            detail_lines.append(f"Tech Stack: {', '.join(tech_stack)}")
        if constraints:
            detail_lines.append(f"Constraints: {', '.join(constraints)}")
        detail_lines.append(f"Created: {project['created_at']}")
        detail_lines.append(f"Updated: {project['updated_at']}")

        return self._create_above_fold_response(
            "SUCCESS", key_info, action_info, details="\n".join(detail_lines)
        )

    async def _get_project_details_status(self, project_id: str) -> list[TextContent]:
        """Status level: summary + per-status breakdowns, completion %, blocked items."""
        # Fetch project name
        summary = await self.db.execute_query(
            "SELECT name, status FROM project_summary WHERE id = ?",
            [project_id],
            fetch_one=True,
            row_factory=True,
        )
        project_name = summary["name"] if summary else project_id
        project_status = summary["status"] if summary else "unknown"

        # Requirement breakdown
        req_stats = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM requirements "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id], fetch_all=True, row_factory=True,
        )
        total_reqs = sum(r["count"] for r in req_stats) if req_stats else 0
        req_parts = ", ".join(f"{r['count']} {r['status']}" for r in req_stats) if req_stats else ""

        # Task breakdown
        task_stats = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id], fetch_all=True, row_factory=True,
        )
        total_tasks = sum(t["count"] for t in task_stats) if task_stats else 0
        completed_tasks = next((t["count"] for t in task_stats if t["status"] == "Validated"), 0) if task_stats else 0
        completion_pct = round(completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        task_parts = ", ".join(f"{t['count']} {t['status']}" for t in task_stats) if task_stats else ""

        # ADR breakdown
        adr_stats = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM architecture "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id], fetch_all=True, row_factory=True,
        )
        total_adrs = sum(a["count"] for a in adr_stats) if adr_stats else 0
        adr_parts = ", ".join(f"{a['count']} {a['status']}" for a in adr_stats) if adr_stats else ""

        # Build report
        lines = [
            f"[SUCCESS] Project {project_id}: {project_name} [{project_status}]",
            "",
            f"Requirements: {total_reqs} total" + (f" ({req_parts})" if req_parts else ""),
            f"Tasks: {total_tasks} total" + (f" ({task_parts})" if task_parts else "") + (f" -- {completion_pct}% complete" if total_tasks > 0 else ""),
            f"Architecture: {total_adrs} total" + (f" ({adr_parts})" if adr_parts else ""),
        ]

        return [TextContent(type="text", text="\n".join(lines))]

    async def _get_project_details_metrics(self, project_id: str) -> list[TextContent]:
        """Metrics level: structured JSON with all breakdowns."""
        # Requirements
        req_by_status = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM requirements "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id], fetch_all=True, row_factory=True,
        )
        req_by_priority = await self.db.execute_query(
            "SELECT priority, COUNT(*) as count FROM requirements "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY priority",
            [project_id], fetch_all=True, row_factory=True,
        )

        # Tasks
        task_by_status = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id], fetch_all=True, row_factory=True,
        )
        task_by_priority = await self.db.execute_query(
            "SELECT priority, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY priority",
            [project_id], fetch_all=True, row_factory=True,
        )
        task_by_assignee = await self.db.execute_query(
            "SELECT COALESCE(assignee, 'Unassigned') as assignee, COUNT(*) as count FROM tasks "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY assignee",
            [project_id], fetch_all=True, row_factory=True,
        )

        # Architecture
        adr_by_status = await self.db.execute_query(
            "SELECT status, COUNT(*) as count FROM architecture "
            "WHERE project_id = ? AND is_archived = 0 GROUP BY status",
            [project_id], fetch_all=True, row_factory=True,
        )

        # Assemble metrics
        total_tasks = sum(t["count"] for t in task_by_status) if task_by_status else 0
        completed_tasks = next((t["count"] for t in task_by_status if t["status"] == "Validated"), 0) if task_by_status else 0
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
        }

        return [TextContent(type="text", text=json.dumps(metrics))]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _count_entities(self, table: str, project_id: str, status: str | None = None) -> int:
        """Count non-archived entities in a table, optionally filtered by status."""
        if status:
            row = await self.db.execute_query(
                f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ? AND status = ? AND is_archived = 0",
                [project_id, status], fetch_one=True, row_factory=True,
            )
        else:
            row = await self.db.execute_query(
                f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ? AND is_archived = 0",
                [project_id], fetch_one=True, row_factory=True,
            )
        return row["cnt"]
