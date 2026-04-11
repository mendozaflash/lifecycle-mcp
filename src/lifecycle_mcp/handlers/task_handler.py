#!/usr/bin/env python3
"""
Task Handler for MCP Lifecycle Management Server (v2)

Handles all task-related operations using the v2 schema:
- Sequential IDs via generate_id("task")
- Project-scoped tasks (project_id FK)
- Polymorphic relationships table (no requirement_tasks)
- Planning fields (scope_boundaries, technical_outline, etc.)
- Execution fields (execution_notes, deviation_from_plan)
- No GitHub integration
"""

import json
from typing import Any

from mcp.types import TextContent

from lifecycle_mcp.constants import TASK_STATUSES, TASK_TRANSITIONS

from .base_handler import BaseHandler


class TaskHandler(BaseHandler):
    """Handler for task-related MCP tools (v2 schema)"""

    # Fields that may be updated via update_task (planning tool)
    _UPDATABLE_FIELDS = [
        "title", "priority", "effort", "user_story", "assignee",
        "parent_task_id", "scope_boundaries", "technical_outline", "risk_notes",
    ]
    _UPDATABLE_JSON_FIELDS = [
        "acceptance_criteria", "files_touched", "verification_commands", "public_symbols",
    ]

    def __init__(self, db_manager):
        super().__init__(db_manager)

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return task tool definitions"""
        return [
            {
                "name": "create_task",
                "description": "Create implementation task linked to a project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                        "effort": {"type": "string", "enum": ["XS", "S", "M", "L", "XL"]},
                        "user_story": {"type": "string"},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "assignee": {"type": "string"},
                        "parent_task_id": {"type": "string"},
                        "scope_boundaries": {"type": "string"},
                        "technical_outline": {"type": "string"},
                        "files_touched": {"type": "array", "items": {"type": "string"}},
                        "verification_commands": {"type": "array", "items": {"type": "string"}},
                        "public_symbols": {"type": "array", "items": {"type": "string"}},
                        "risk_notes": {"type": "string"},
                    },
                    "required": ["project_id", "title", "priority"],
                },
            },
            {
                "name": "update_task",
                "description": "Update task planning fields (title, priority, effort, planning fields, etc.)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                        "effort": {"type": "string", "enum": ["XS", "S", "M", "L", "XL"]},
                        "user_story": {"type": "string"},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "assignee": {"type": "string"},
                        "parent_task_id": {"type": "string"},
                        "scope_boundaries": {"type": "string"},
                        "technical_outline": {"type": "string"},
                        "files_touched": {"type": "array", "items": {"type": "string"}},
                        "verification_commands": {"type": "array", "items": {"type": "string"}},
                        "public_symbols": {"type": "array", "items": {"type": "string"}},
                        "risk_notes": {"type": "string"},
                    },
                    "required": ["task_id"],
                },
            },
            {
                "name": "update_task_status",
                "description": (
                    "Update task progress (narrow write: only status, execution_notes, "
                    "deviation_from_plan). Validates state transitions."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "new_status": {
                            "type": "string",
                            "enum": ["Under Review", "Approved", "Implemented", "Validated", "Deprecated"],
                        },
                        "execution_notes": {"type": "string"},
                        "deviation_from_plan": {"type": "string"},
                    },
                    "required": ["task_id", "new_status"],
                },
            },
            {
                "name": "archive_task",
                "description": "Archive a task (soft delete)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string", "description": "Task ID (TASK-XXXX)"},
                    },
                    "required": ["task_id"],
                },
            },
            {
                "name": "query_tasks",
                "description": "Search and filter tasks",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "status": {"type": "string"},
                        "priority": {"type": "string"},
                        "assignee": {"type": "string"},
                        "include_archived": {
                            "type": "boolean",
                            "description": "Include archived tasks (default: false)",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["summary", "json", "markdown"],
                            "description": "Output format: summary (one-line per task), json (structured array), markdown (verbose). Default: summary",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of tasks to return (default: 25)",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of tasks to skip for pagination (default: 0)",
                        },
                    },
                },
            },
            {
                "name": "get_task_details",
                "description": "Get task details with configurable sections",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "sections": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": ["planning", "execution", "requirements", "adrs", "subtasks"],
                            },
                            "description": "Sections to include. Default: ['planning', 'requirements']",
                        },
                    },
                    "required": ["task_id"],
                },
            },
            {
                "name": "batch_create_tasks",
                "description": "Create multiple tasks atomically (all or nothing)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "tasks": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "title": {"type": "string"},
                                    "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                                    "effort": {"type": "string", "enum": ["XS", "S", "M", "L", "XL"]},
                                    "user_story": {"type": "string"},
                                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                                    "assignee": {"type": "string"},
                                    "parent_task_id": {"type": "string"},
                                    "scope_boundaries": {"type": "string"},
                                    "technical_outline": {"type": "string"},
                                    "files_touched": {"type": "array", "items": {"type": "string"}},
                                    "verification_commands": {"type": "array", "items": {"type": "string"}},
                                    "public_symbols": {"type": "array", "items": {"type": "string"}},
                                    "risk_notes": {"type": "string"},
                                },
                                "required": ["title", "priority"],
                            },
                            "description": "Array of task objects to create",
                        },
                    },
                    "required": ["project_id", "tasks"],
                },
            },
            {
                "name": "clone_task",
                "description": (
                    "Clone a task with a new ID. Copies relationships. Resets status to Under Review. "
                    "Optionally clones child tasks recursively."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "include_children": {
                            "type": "boolean",
                            "description": "Recursively clone child tasks (default: false)",
                        },
                        "target_project_id": {
                            "type": "string",
                            "description": "Clone into a different project (default: same project)",
                        },
                    },
                    "required": ["task_id"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Route tool calls to handler methods"""
        handlers = {
            "create_task": self._create_task,
            "update_task": self._update_task,
            "update_task_status": self._update_task_status,
            "archive_task": self._archive_task,
            "query_tasks": self._query_tasks,
            "get_task_details": self._get_task_details,
            "batch_create_tasks": self._batch_create_tasks,
            "clone_task": self._clone_task,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._create_error_response(f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return self._create_error_response(f"Error handling {tool_name}", e)

    # ------------------------------------------------------------------
    # create_task
    # ------------------------------------------------------------------

    async def _create_task(self, params: dict[str, Any]) -> list[TextContent]:
        """Create a task linked to a project."""
        error = self._validate_required_params(params, ["project_id", "title", "priority"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        task_id, _ = await self.db.generate_id("task")
        data = self._build_task_data(task_id, project_id, params)
        await self.db.insert_record("tasks", data)
        await self._log_operation("task", task_id, "created", project_id=project_id)

        key_info = f"Task {task_id} created"
        action_info = f"{params['title']} | {params['priority']} | {params.get('effort', 'No effort')}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # update_task (broad planning update)
    # ------------------------------------------------------------------

    async def _update_task(self, params: dict[str, Any]) -> list[TextContent]:
        """Update task planning fields."""
        error = self._validate_required_params(params, ["task_id"])
        if error:
            return self._create_error_response(error)

        task_id = params["task_id"]
        error = await self._validate_not_archived("task", task_id)
        if error:
            return self._create_error_response(error)

        data: dict[str, Any] = {}
        for field in self._UPDATABLE_FIELDS:
            if field in params and params[field] is not None:
                data[field] = params[field]
        for field in self._UPDATABLE_JSON_FIELDS:
            if field in params and params[field] is not None:
                data[field] = self._safe_json_dumps(params[field])

        if not data:
            return self._create_error_response("No fields to update")

        await self.db.update_record("tasks", data, "id = ?", [task_id])
        await self._log_operation("task", task_id, "updated")

        return self._create_above_fold_response(
            "SUCCESS",
            f"Task {task_id} updated",
            f"Updated fields: {', '.join(data.keys())}",
        )

    # ------------------------------------------------------------------
    # update_task_status (narrow write)
    # ------------------------------------------------------------------

    async def _update_task_status(self, params: dict[str, Any]) -> list[TextContent]:
        """Update task status with transition validation. Narrow write."""
        error = self._validate_required_params(params, ["task_id", "new_status"])
        if error:
            return self._create_error_response(error)

        task_id = params["task_id"]
        new_status = params["new_status"]

        if new_status not in TASK_STATUSES:
            return self._create_error_response(
                f"Invalid status '{new_status}'. Valid: {', '.join(sorted(TASK_STATUSES))}"
            )

        # Get current task
        rows = await self.db.get_records("tasks", "status", where_clause="id = ?", where_params=[task_id])
        if not rows:
            return self._create_error_response(f"Task not found: {task_id}")

        current_status = rows[0]["status"]
        allowed = TASK_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            return self._create_error_response(
                f"Invalid transition from '{current_status}' to '{new_status}'. "
                f"Allowed transitions: {allowed or 'none (terminal state)'}"
            )

        # Task approval gating: require all linked requirements to be Approved
        if new_status == "Approved":
            gating_error = await self._check_requirement_approval_gating(task_id)
            if gating_error:
                return self._create_error_response(gating_error)

        # NARROW WRITE: only status + execution fields
        data: dict[str, Any] = {"status": new_status}
        if "execution_notes" in params and params["execution_notes"] is not None:
            data["execution_notes"] = params["execution_notes"]
        if "deviation_from_plan" in params and params["deviation_from_plan"] is not None:
            data["deviation_from_plan"] = params["deviation_from_plan"]

        await self.db.update_record("tasks", data, "id = ?", [task_id])

        key_info = f"Task {task_id} updated"
        action_info = f"{current_status} -> {new_status}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # archive_task
    # ------------------------------------------------------------------

    async def _archive_task(self, params: dict[str, Any]) -> list[TextContent]:
        """Archive a task (soft delete)."""
        error = self._validate_required_params(params, ["task_id"])
        if error:
            return self._create_error_response(error)

        task_id = params["task_id"]
        error = await self._validate_entity_exists("task", task_id)
        if error:
            return self._create_error_response(error)

        await self.db.execute_query(
            "UPDATE tasks SET is_archived = 1, archived_at = datetime('now') WHERE id = ?",
            [task_id],
        )
        await self._log_operation("task", task_id, "archived")

        return self._create_above_fold_response("SUCCESS", f"Task {task_id} archived")

    # ------------------------------------------------------------------
    # query_tasks
    # ------------------------------------------------------------------

    async def _query_tasks(self, params: dict[str, Any]) -> list[TextContent]:
        """Query tasks with filters, output_format, limit, and offset."""
        output_format = params.get("output_format", "summary")
        limit = params.get("limit", 25)
        offset = params.get("offset", 0)

        conditions, query_params = self._build_query_filters(params)
        where_clause = " AND ".join(conditions) if conditions else ""

        # Build query with LIMIT/OFFSET
        query = "SELECT * FROM tasks"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += " ORDER BY priority, created_at DESC"
        query += " LIMIT ? OFFSET ?"
        query_params.extend([limit, offset])

        tasks = await self.db.execute_query(
            query, query_params, fetch_all=True, row_factory=True
        )

        if not tasks:
            return self._create_above_fold_response("INFO", "No tasks found", "Try adjusting search criteria")

        if output_format == "json":
            return self._format_tasks_json(tasks)
        elif output_format == "markdown":
            return self._format_tasks_markdown(tasks, params)
        else:
            return self._format_tasks_summary(tasks, params)

    def _format_tasks_summary(self, tasks: list, params: dict[str, Any]) -> list[TextContent]:
        """Format tasks as one-line pipe-delimited summaries."""
        lines = []
        for task in tasks:
            line = f"TASK {task['id']} | {task['title']} | {task['status']} | {task['priority']}"
            if task["assignee"]:
                line += f" | {task['assignee']}"
            lines.append(line)

        filter_desc = self._build_filter_description(params)
        key_info = self._format_count_summary("task", len(tasks), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

    def _format_tasks_json(self, tasks: list) -> list[TextContent]:
        """Format tasks as a JSON array of {id, title, status, priority}."""
        result_list = [
            {
                "id": task["id"],
                "title": task["title"],
                "status": task["status"],
                "priority": task["priority"],
            }
            for task in tasks
        ]
        return [TextContent(type="text", text=json.dumps(result_list))]

    def _format_tasks_markdown(self, tasks: list, params: dict[str, Any]) -> list[TextContent]:
        """Format tasks as verbose markdown (backward-compatible with old query_tasks)."""
        lines = []
        for task in tasks:
            info = f"- {task['id']}: {task['title']} [{task['status']}] {task['priority']}"
            if task["assignee"]:
                info += f" ({task['assignee']})"
            lines.append(info)

        filter_desc = self._build_filter_description(params)
        key_info = self._format_count_summary("task", len(tasks), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

    # ------------------------------------------------------------------
    # get_task_details
    # ------------------------------------------------------------------

    async def _get_task_details(self, params: dict[str, Any]) -> list[TextContent]:
        """Get task details with configurable sections."""
        error = self._validate_required_params(params, ["task_id"])
        if error:
            return self._create_error_response(error)

        task_id = params["task_id"]
        rows = await self.db.get_records("tasks", "*", where_clause="id = ?", where_params=[task_id])
        if not rows:
            return self._create_error_response(f"Task not found: {task_id}")

        task = dict(rows[0])
        sections = params.get("sections", ["planning", "requirements"])

        # Build report — basic info is always included
        report = f"""# Task Details: {task["id"]}

## Basic Information
- **Title**: {task["title"]}
- **Status**: {task["status"]}
- **Priority**: {task["priority"]}
- **Effort**: {task["effort"] or "Not specified"}
- **Assignee**: {task["assignee"] or "Unassigned"}
- **Project**: {task["project_id"]}
- **Created**: {task["created_at"]}
- **Updated**: {task["updated_at"]}

## Description
{task["user_story"] or "No user story provided"}

## Acceptance Criteria
"""
        criteria = self._safe_json_loads(task.get("acceptance_criteria"))
        if criteria:
            for c in criteria:
                report += f"- {c}\n"
        else:
            report += "No acceptance criteria defined\n"

        # Planning section
        if "planning" in sections:
            planning_items = []
            if task.get("scope_boundaries"):
                planning_items.append(f"- **Scope Boundaries**: {task['scope_boundaries']}")
            if task.get("technical_outline"):
                planning_items.append(f"- **Technical Outline**: {task['technical_outline']}")
            if task.get("files_touched"):
                files = self._safe_json_loads(task["files_touched"])
                if files:
                    planning_items.append(f"- **Files Touched**: {', '.join(files)}")
            if task.get("verification_commands"):
                cmds = self._safe_json_loads(task["verification_commands"])
                if cmds:
                    planning_items.append(f"- **Verification Commands**: {', '.join(cmds)}")
            if task.get("public_symbols"):
                syms = self._safe_json_loads(task["public_symbols"])
                if syms:
                    planning_items.append(f"- **Public Symbols**: {', '.join(syms)}")
            if task.get("risk_notes"):
                planning_items.append(f"- **Risk Notes**: {task['risk_notes']}")

            if planning_items:
                report += "\n## Planning\n" + "\n".join(planning_items) + "\n"

        # Execution section
        if "execution" in sections:
            exec_items = []
            if task.get("execution_notes"):
                exec_items.append(f"- **Execution Notes**: {task['execution_notes']}")
            if task.get("deviation_from_plan"):
                exec_items.append(f"- **Deviation from Plan**: {task['deviation_from_plan']}")
            if task.get("completed_at"):
                exec_items.append(f"- **Completed At**: {task['completed_at']}")

            if exec_items:
                report += "\n## Execution\n" + "\n".join(exec_items) + "\n"

        # Requirements section
        if "requirements" in sections:
            requirements = await self._get_linked_requirements(task_id)
            if requirements:
                report += f"\n## Linked Requirements ({len(requirements)})\n"
                for req in requirements:
                    report += f"- {req['id']}: {req['title']}\n"

        # ADRs section
        if "adrs" in sections:
            adrs = await self._get_linked_adrs(task_id)
            if adrs:
                report += f"\n## Linked Architecture Decisions ({len(adrs)})\n"
                for adr in adrs:
                    report += f"- {adr['id']}: {adr['title']}\n"

        # Subtasks section
        if "subtasks" in sections:
            children = await self.db.get_records(
                "tasks", "id, title, status",
                where_clause="parent_task_id = ? AND is_archived = 0",
                where_params=[task_id],
            )
            if children:
                report += f"\n## Subtasks ({len(children)})\n"
                for child in children:
                    report += f"- {child['id']}: {child['title']} [{child['status']}]\n"

            if task.get("parent_task_id"):
                parent_rows = await self.db.get_records(
                    "tasks", "id, title, status",
                    where_clause="id = ?",
                    where_params=[task["parent_task_id"]],
                )
                if parent_rows:
                    p = parent_rows[0]
                    report += f"\n## Parent Task\n- {p['id']}: {p['title']} [{p['status']}]\n"

        key_info = self._format_status_summary("Task", task["id"], task["status"])
        action_info = f"{task['title']} | {task['priority']} | {task['effort'] or 'No effort'}"
        if task["assignee"]:
            action_info += f" | {task['assignee']}"

        return self._create_above_fold_response("INFO", key_info, action_info, report)

    # ------------------------------------------------------------------
    # batch_create_tasks
    # ------------------------------------------------------------------

    async def _batch_create_tasks(self, params: dict[str, Any]) -> list[TextContent]:
        """Create multiple tasks atomically."""
        error = self._validate_required_params(params, ["project_id", "tasks"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        task_defs = params["tasks"]

        if not task_defs:
            return self._create_error_response("No tasks provided in batch")

        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        # Validate all tasks before creating any
        for i, task_def in enumerate(task_defs):
            if "title" not in task_def or not task_def["title"]:
                return self._create_error_response(
                    f"Task at index {i} is missing required field: title"
                )
            if "priority" not in task_def or not task_def["priority"]:
                return self._create_error_response(
                    f"Task at index {i} is missing required field: priority"
                )

        # Create all tasks
        created_ids = []
        for task_def in task_defs:
            task_id, _ = await self.db.generate_id("task")
            data = self._build_task_data(task_id, project_id, task_def)
            await self.db.insert_record("tasks", data)
            created_ids.append(task_id)

        await self._log_operation("task", f"batch:{len(created_ids)}", "batch_created", project_id=project_id)

        ids_str = ", ".join(created_ids)
        return self._create_above_fold_response(
            "SUCCESS",
            f"Created {len(created_ids)} tasks",
            ids_str,
        )

    # ------------------------------------------------------------------
    # clone_task
    # ------------------------------------------------------------------

    async def _clone_task(self, params: dict[str, Any]) -> list[TextContent]:
        """Clone a task with a new ID."""
        error = self._validate_required_params(params, ["task_id"])
        if error:
            return self._create_error_response(error)

        task_id = params["task_id"]
        include_children = params.get("include_children", False)
        target_project_id = params.get("target_project_id")

        # Get original task
        rows = await self.db.get_records("tasks", "*", where_clause="id = ?", where_params=[task_id])
        if not rows:
            return self._create_error_response(f"Task not found: {task_id}")

        original = dict(rows[0])

        # Validate target project if specified
        project_id = target_project_id or original["project_id"]
        if target_project_id:
            error = await self._validate_project_exists(target_project_id)
            if error:
                return self._create_error_response(error)

        # Clone the task (and optionally children)
        cloned_ids = await self._clone_task_recursive(
            original, project_id, include_children, parent_task_id=None
        )

        ids_str = ", ".join(cloned_ids)
        return self._create_above_fold_response(
            "SUCCESS",
            f"Cloned {len(cloned_ids)} task(s)",
            ids_str,
        )

    async def _clone_task_recursive(
        self,
        original: dict[str, Any],
        project_id: str,
        include_children: bool,
        parent_task_id: str | None,
    ) -> list[str]:
        """Recursively clone a task and optionally its children."""
        new_id, _ = await self.db.generate_id("task")

        # Build clone data - copy planning fields, reset execution fields
        clone_data: dict[str, Any] = {
            "id": new_id,
            "project_id": project_id,
            "title": original["title"],
            "status": "Under Review",
            "priority": original["priority"],
        }

        # Copy optional fields
        for field in ["effort", "user_story", "acceptance_criteria", "assignee",
                       "scope_boundaries", "technical_outline", "files_touched",
                       "verification_commands", "public_symbols", "risk_notes"]:
            if original.get(field) is not None:
                clone_data[field] = original[field]

        # Set parent if provided (for recursive cloning)
        if parent_task_id:
            clone_data["parent_task_id"] = parent_task_id

        await self.db.insert_record("tasks", clone_data)

        # Copy relationships (where original task is the source)
        rels = await self.db.get_records(
            "relationships",
            where_clause="source_id = ? AND source_type = 'task'",
            where_params=[original["id"]],
        )
        for rel in rels:
            # Skip parent relationships (handled by parent_task_id)
            if rel["relationship_type"] == "parent":
                continue
            clone_rel_id = f"rel-{new_id}-{rel['target_id']}-{rel['relationship_type']}"
            await self.db.insert_record("relationships", {
                "id": clone_rel_id,
                "source_type": "task",
                "source_id": new_id,
                "target_type": rel["target_type"],
                "target_id": rel["target_id"],
                "relationship_type": rel["relationship_type"],
                "project_id": project_id,
            })

        cloned_ids = [new_id]

        # Recursively clone children if requested
        if include_children:
            children = await self.db.get_records(
                "tasks",
                where_clause="parent_task_id = ? AND is_archived = 0",
                where_params=[original["id"]],
            )
            for child in children:
                child_dict = dict(child)
                child_cloned = await self._clone_task_recursive(
                    child_dict, project_id, include_children=True, parent_task_id=new_id
                )
                cloned_ids.extend(child_cloned)

        return cloned_ids

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _check_requirement_approval_gating(self, task_id: str) -> str | None:
        """Check if all linked requirements are in 'Approved' status.
        Returns error message if gating fails, None if OK."""
        linked_reqs = await self._get_linked_requirements(task_id)
        if not linked_reqs:
            return None  # No requirements = ungated
        non_approved = [
            f"{req['id']} ({req['status']})"
            for req in linked_reqs
            if req['status'] != 'Approved'
        ]
        if non_approved:
            return (
                f"Cannot approve task: linked requirement(s) not in 'Approved' status: "
                f"{', '.join(non_approved)}. All linked requirements must be exactly 'Approved'."
            )
        return None

    def _build_task_data(self, task_id: str, project_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Build a task data dict for INSERT."""
        data: dict[str, Any] = {
            "id": task_id,
            "project_id": project_id,
            "title": params["title"],
            "priority": params["priority"],
            "status": "Under Review",
        }
        # Optional scalar fields
        for field in ["effort", "user_story", "assignee", "parent_task_id",
                       "scope_boundaries", "technical_outline", "risk_notes"]:
            if field in params and params[field] is not None:
                data[field] = params[field]
        # Optional JSON array fields
        for field in ["acceptance_criteria", "files_touched", "verification_commands", "public_symbols"]:
            if field in params and params[field] is not None:
                data[field] = self._safe_json_dumps(params[field])
        return data

    def _build_query_filters(self, params: dict[str, Any]) -> tuple[list[str], list[Any]]:
        """Build WHERE conditions for query_tasks."""
        conditions: list[str] = []
        query_params: list[Any] = []

        include_archived = params.get("include_archived", False)
        if not include_archived:
            conditions.append("is_archived = 0")

        if params.get("project_id"):
            conditions.append("project_id = ?")
            query_params.append(params["project_id"])
        if params.get("status"):
            conditions.append("status = ?")
            query_params.append(params["status"])
        if params.get("priority"):
            conditions.append("priority = ?")
            query_params.append(params["priority"])
        if params.get("assignee"):
            conditions.append("assignee = ?")
            query_params.append(params["assignee"])

        return conditions, query_params

    def _build_filter_description(self, params: dict[str, Any]) -> str:
        """Build a human-readable filter description."""
        filters = []
        if params.get("project_id"):
            filters.append(f"project: {params['project_id']}")
        if params.get("status"):
            filters.append(f"status: {params['status']}")
        if params.get("priority"):
            filters.append(f"priority: {params['priority']}")
        if params.get("assignee"):
            filters.append(f"assignee: {params['assignee']}")
        return " | ".join(filters) if filters else "all tasks"

    async def _get_linked_requirements(self, task_id: str) -> list:
        """Get requirements linked to a task via relationships (both directions)."""
        return await self.db.execute_query(
            """
            SELECT DISTINCT r.* FROM requirements r
            JOIN relationships rel ON
                (rel.source_id = ? AND rel.target_id = r.id AND rel.target_type = 'requirement')
                OR (rel.target_id = ? AND rel.source_id = r.id AND rel.source_type = 'requirement')
            WHERE r.is_archived = 0
            """,
            [task_id, task_id],
            fetch_all=True,
            row_factory=True,
        )

    async def _get_linked_adrs(self, task_id: str) -> list:
        """Get architecture decisions linked to a task via relationships (both directions)."""
        return await self.db.execute_query(
            """
            SELECT DISTINCT a.* FROM architecture a
            JOIN relationships rel ON
                (rel.source_id = ? AND rel.target_id = a.id AND rel.target_type = 'architecture')
                OR (rel.target_id = ? AND rel.source_id = a.id AND rel.source_type = 'architecture')
            WHERE a.is_archived = 0
            """,
            [task_id, task_id],
            fetch_all=True,
            row_factory=True,
        )
