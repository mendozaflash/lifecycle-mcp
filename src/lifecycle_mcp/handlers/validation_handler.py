#!/usr/bin/env python3
"""
Validation Handler for MCP Lifecycle Management Server (v2)

Provides project-level validation (linting) and status transition lookups:
  - validate_project_plan: checks orphans, cycles, missing fields, blocked tasks, invalid states
  - get_valid_status_transitions: returns allowed next statuses for a given entity/status
"""

import json
import os
from typing import Any

from mcp.types import TextContent

from lifecycle_mcp.constants import STATE_MACHINES

from .base_handler import BaseHandler


class ValidationHandler(BaseHandler):
    """Handler for validation and status-transition MCP tools."""

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "validate_project_plan",
                "description": (
                    "Validate project plan for orphans, cycles, missing fields, "
                    "and invalid states"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (PROJ-XXXX)",
                        },
                        "output_directory": {
                            "type": "string",
                            "description": (
                                "If provided, writes REQUIREMENTS_STATUS.md, "
                                "TASK_STATUS.md, ADR_STATUS.md to this directory"
                            ),
                        },
                    },
                    "required": ["project_id"],
                },
            },
            {
                "name": "get_valid_status_transitions",
                "description": "Get valid status transitions for an entity type",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity_type": {
                            "type": "string",
                            "enum": ["requirement", "task", "architecture"],
                        },
                        "current_status": {"type": "string"},
                    },
                    "required": ["entity_type", "current_status"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def handle_tool_call(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> list[TextContent]:
        try:
            if tool_name == "validate_project_plan":
                return await self._validate_project_plan(arguments)
            elif tool_name == "get_valid_status_transitions":
                return await self._get_valid_status_transitions(arguments)
            else:
                return self._create_error_response(f"Unknown tool: {tool_name}")
        except Exception as exc:
            return self._create_error_response(
                f"Error in {tool_name}", exception=exc
            )

    # ------------------------------------------------------------------
    # validate_project_plan
    # ------------------------------------------------------------------

    async def _validate_project_plan(
        self, params: dict[str, Any]
    ) -> list[TextContent]:
        # --- param validation ---
        err = self._validate_required_params(params, ["project_id"])
        if err:
            return self._create_error_response(err)

        project_id: str = params["project_id"]
        output_directory: str | None = params.get("output_directory")

        # --- project existence ---
        proj_err = await self._validate_project_exists(project_id)
        if proj_err:
            return self._create_error_response(proj_err)

        # Accumulators
        details: list[dict[str, Any]] = []
        errors = 0
        warnings = 0
        infos = 0

        # ----------------------------------------------------------
        # Fetch project entities (non-archived only)
        # ----------------------------------------------------------
        requirements = await self.db.get_records(
            "requirements",
            where_clause="project_id = ? AND is_archived = 0",
            where_params=[project_id],
        )
        tasks = await self.db.get_records(
            "tasks",
            where_clause="project_id = ? AND is_archived = 0",
            where_params=[project_id],
        )
        adrs = await self.db.get_records(
            "architecture",
            where_clause="project_id = ? AND is_archived = 0",
            where_params=[project_id],
        )

        # All relationships for this project
        relationships = await self.db.get_records(
            "relationships",
            where_clause="project_id = ?",
            where_params=[project_id],
        )

        # Build lookup sets for relationship endpoints
        # A requirement is "linked to a task" if any relationship has
        # (source=req AND target is a task) OR (target=req AND source is a task)
        req_ids = {r["id"] for r in requirements}
        task_ids = {t["id"] for t in tasks}
        adr_ids = {a["id"] for a in adrs}

        reqs_linked_to_tasks: set[str] = set()
        tasks_linked_to_reqs: set[str] = set()
        adrs_linked_to_reqs: set[str] = set()

        for rel in relationships:
            src = rel["source_id"]
            tgt = rel["target_id"]
            # req <-> task links
            if src in req_ids and tgt in task_ids:
                reqs_linked_to_tasks.add(src)
                tasks_linked_to_reqs.add(tgt)
            elif src in task_ids and tgt in req_ids:
                tasks_linked_to_reqs.add(src)
                reqs_linked_to_tasks.add(tgt)
            # adr <-> req links
            if src in adr_ids and tgt in req_ids:
                adrs_linked_to_reqs.add(src)
            elif src in req_ids and tgt in adr_ids:
                adrs_linked_to_reqs.add(tgt)

        # ----------------------------------------------------------
        # 1. Orphan requirements
        # ----------------------------------------------------------
        for req in requirements:
            if req["id"] not in reqs_linked_to_tasks:
                warnings += 1
                details.append({
                    "check": "orphan_requirement",
                    "severity": "warning",
                    "entity_id": req["id"],
                    "message": f"Requirement {req['id']} has no linked tasks",
                })

        # ----------------------------------------------------------
        # 2. Orphan tasks
        # ----------------------------------------------------------
        for task in tasks:
            if task["id"] not in tasks_linked_to_reqs:
                warnings += 1
                details.append({
                    "check": "orphan_task",
                    "severity": "warning",
                    "entity_id": task["id"],
                    "message": f"Task {task['id']} has no linked requirements",
                })

        # ----------------------------------------------------------
        # 3. Dependency cycles (task -> task via depends/blocks)
        # ----------------------------------------------------------
        adjacency: dict[str, list[str]] = {tid: [] for tid in task_ids}
        for rel in relationships:
            if rel["relationship_type"] in ("depends", "blocks"):
                src = rel["source_id"]
                tgt = rel["target_id"]
                if src in task_ids and tgt in task_ids:
                    adjacency.setdefault(src, []).append(tgt)

        cycles = self._detect_cycles(adjacency)
        for cycle in cycles:
            errors += 1
            details.append({
                "check": "dependency_cycle",
                "severity": "error",
                "entity_id": " -> ".join(cycle),
                "message": f"Dependency cycle detected: {' -> '.join(cycle)}",
            })

        # ----------------------------------------------------------
        # 4. Missing acceptance criteria
        # ----------------------------------------------------------
        for req in requirements:
            ac = req["acceptance_criteria"]
            if ac is None or ac == "" or ac == "[]":
                warnings += 1
                details.append({
                    "check": "missing_acceptance_criteria",
                    "severity": "warning",
                    "entity_id": req["id"],
                    "message": f"Requirement {req['id']} has no acceptance criteria",
                })

        # ----------------------------------------------------------
        # 5. Missing planning fields
        # ----------------------------------------------------------
        for task in tasks:
            scope = task["scope_boundaries"]
            outline = task["technical_outline"]
            if (not scope or scope.strip() == "") and (not outline or outline.strip() == ""):
                warnings += 1
                details.append({
                    "check": "missing_planning_fields",
                    "severity": "warning",
                    "entity_id": task["id"],
                    "message": (
                        f"Task {task['id']} has no scope_boundaries "
                        f"or technical_outline"
                    ),
                })

        # ----------------------------------------------------------
        # 6. Blocked tasks
        # ----------------------------------------------------------
        for task in tasks:
            if task["status"] == "Blocked":
                infos += 1
                details.append({
                    "check": "blocked_task",
                    "severity": "info",
                    "entity_id": task["id"],
                    "message": f"Task {task['id']} is blocked",
                })

        # ----------------------------------------------------------
        # 7. Unlinked ADRs
        # ----------------------------------------------------------
        for adr in adrs:
            if adr["id"] not in adrs_linked_to_reqs:
                warnings += 1
                details.append({
                    "check": "unlinked_adr",
                    "severity": "warning",
                    "entity_id": adr["id"],
                    "message": f"Architecture decision {adr['id']} has no linked requirements",
                })

        # ----------------------------------------------------------
        # 8. Invalid status combinations (superseded but not deprecated)
        # ----------------------------------------------------------
        for adr in adrs:
            if adr["superseded_by"] and adr["status"] != "Deprecated":
                errors += 1
                details.append({
                    "check": "invalid_status_combination",
                    "severity": "error",
                    "entity_id": adr["id"],
                    "message": (
                        f"ADR {adr['id']} is superseded by {adr['superseded_by']} "
                        f"but status is '{adr['status']}' instead of 'Deprecated'"
                    ),
                })

        # ----------------------------------------------------------
        # Build result
        # ----------------------------------------------------------
        result: dict[str, Any] = {
            "errors": errors,
            "warnings": warnings,
            "infos": infos,
            "details": details,
        }

        # ----------------------------------------------------------
        # Optional: write markdown status files
        # ----------------------------------------------------------
        if output_directory:
            os.makedirs(output_directory, exist_ok=True)

            self._write_requirements_status(
                os.path.join(output_directory, "REQUIREMENTS_STATUS.md"),
                requirements,
                project_id,
            )
            self._write_tasks_status(
                os.path.join(output_directory, "TASK_STATUS.md"),
                tasks,
                project_id,
            )
            self._write_adrs_status(
                os.path.join(output_directory, "ADR_STATUS.md"),
                adrs,
                project_id,
            )
            result["files_written"] = 3

        return self._create_response(json.dumps(result))

    # ----------------------------------------------------------
    # Cycle detection (DFS)
    # ----------------------------------------------------------

    def _detect_cycles(
        self, adjacency: dict[str, list[str]]
    ) -> list[list[str]]:
        """DFS-based cycle detection. Returns list of cycles found."""
        _white, _gray, _black = 0, 1, 2
        color: dict[str, int] = dict.fromkeys(adjacency, _white)
        cycles: list[list[str]] = []
        path: list[str] = []

        def dfs(u: str) -> None:
            color[u] = _gray
            path.append(u)
            for v in adjacency.get(u, []):
                if v == u:
                    # Self-reference
                    cycles.append([u, u])
                elif color.get(v, _white) == _gray:
                    # Back edge found -- extract cycle from path
                    idx = path.index(v)
                    cycle = path[idx:] + [v]
                    cycles.append(cycle)
                elif color.get(v, _white) == _white:
                    dfs(v)
            path.pop()
            color[u] = _black

        for node in adjacency:
            if color[node] == _white:
                dfs(node)

        return cycles

    # ----------------------------------------------------------
    # Markdown file writers
    # ----------------------------------------------------------

    @staticmethod
    def _write_requirements_status(
        filepath: str, requirements: list, project_id: str
    ) -> None:
        lines = [
            f"# Requirements Status - {project_id}",
            "",
            "| ID | Title | Status | Priority | Type |",
            "|---|---|---|---|---|",
        ]
        for r in requirements:
            lines.append(
                f"| {r['id']} | {r['title']} | {r['status']} "
                f"| {r['priority']} | {r['type']} |"
            )
        lines.append("")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    @staticmethod
    def _write_tasks_status(
        filepath: str, tasks: list, project_id: str
    ) -> None:
        lines = [
            f"# Tasks Status - {project_id}",
            "",
            "| ID | Title | Status | Priority | Assignee |",
            "|---|---|---|---|---|",
        ]
        for t in tasks:
            assignee = t["assignee"] if t["assignee"] else "-"
            lines.append(
                f"| {t['id']} | {t['title']} | {t['status']} "
                f"| {t['priority']} | {assignee} |"
            )
        lines.append("")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    @staticmethod
    def _write_adrs_status(
        filepath: str, adrs: list, project_id: str
    ) -> None:
        lines = [
            f"# Architecture Decisions Status - {project_id}",
            "",
            "| ID | Title | Status | Superseded By |",
            "|---|---|---|---|",
        ]
        for a in adrs:
            superseded = a["superseded_by"] if a["superseded_by"] else "-"
            lines.append(
                f"| {a['id']} | {a['title']} | {a['status']} | {superseded} |"
            )
        lines.append("")
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    # ------------------------------------------------------------------
    # get_valid_status_transitions
    # ------------------------------------------------------------------

    async def _get_valid_status_transitions(
        self, params: dict[str, Any]
    ) -> list[TextContent]:
        err = self._validate_required_params(
            params, ["entity_type", "current_status"]
        )
        if err:
            return self._create_error_response(err)

        entity_type: str = params["entity_type"]
        current_status: str = params["current_status"]

        machine = STATE_MACHINES.get(entity_type)
        if machine is None:
            return self._create_error_response(
                f"Unknown entity type: '{entity_type}'. "
                f"Valid types: {', '.join(sorted(STATE_MACHINES))}"
            )

        transitions = machine.get(current_status)
        if transitions is None:
            return self._create_error_response(
                f"Unknown status '{current_status}' for entity type '{entity_type}'. "
                f"Valid statuses: {', '.join(sorted(machine))}"
            )

        result = {
            "entity_type": entity_type,
            "current_status": current_status,
            "valid_transitions": transitions,
        }
        return self._create_response(json.dumps(result))
