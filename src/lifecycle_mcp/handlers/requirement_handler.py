#!/usr/bin/env python3
"""
Requirement Handler for MCP Lifecycle Management Server (v2)

Handles all requirement-related operations using the v2 schema:
- Sequential IDs via generate_id("requirement")
- Project-scoped requirements (project_id FK)
- Polymorphic relationships table (no requirement_tasks)
- No risk_level, no requirement_number, no version column
- Archive (soft delete) support
- Batch create and clone operations
"""

import json
from typing import Any

from mcp.types import TextContent

from lifecycle_mcp.constants import REQUIREMENT_TRANSITIONS

from .base_handler import BaseHandler


class RequirementHandler(BaseHandler):
    """Handler for requirement-related MCP tools (v2 schema)"""

    # Fields that may be updated via update_requirement
    _UPDATABLE_FIELDS = [
        "title", "type", "priority", "current_state", "desired_state",
        "business_value", "author",
    ]
    _UPDATABLE_JSON_FIELDS = [
        "functional_requirements", "nonfunctional_requirements", "out_of_scope",
        "acceptance_criteria",
    ]

    def __init__(self, db_manager, mcp_client=None):
        """Initialize handler with database manager and optional MCP client"""
        super().__init__(db_manager)
        self.mcp_client = mcp_client
        self._testing_mode = False

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return requirement tool definitions"""
        return [
            {
                "name": "create_requirement",
                "description": "Create a new requirement linked to a project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "type": {"type": "string", "enum": ["FUNC", "NFUNC", "TECH", "BUS", "INTF"]},
                        "title": {"type": "string"},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                        "current_state": {"type": "string"},
                        "desired_state": {"type": "string"},
                        "functional_requirements": {"type": "array", "items": {"type": "string"}},
                        "nonfunctional_requirements": {"type": "array", "items": {"type": "string"}},
                        "out_of_scope": {"type": "array", "items": {"type": "string"}},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "business_value": {"type": "string"},
                        "author": {"type": "string"},
                    },
                    "required": ["project_id", "type", "title", "priority"],
                },
            },
            {
                "name": "update_requirement",
                "description": "Update requirement fields (title, priority, type, etc.)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string"},
                        "title": {"type": "string"},
                        "type": {"type": "string", "enum": ["FUNC", "NFUNC", "TECH", "BUS", "INTF"]},
                        "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                        "current_state": {"type": "string"},
                        "desired_state": {"type": "string"},
                        "functional_requirements": {"type": "array", "items": {"type": "string"}},
                        "nonfunctional_requirements": {"type": "array", "items": {"type": "string"}},
                        "out_of_scope": {"type": "array", "items": {"type": "string"}},
                        "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                        "business_value": {"type": "string"},
                        "author": {"type": "string"},
                    },
                    "required": ["requirement_id"],
                },
            },
            {
                "name": "update_requirement_status",
                "description": "Move requirement through lifecycle states with transition validation",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string"},
                        "new_status": {
                            "type": "string",
                            "enum": [
                                "Draft", "Under Review", "Approved", "Architecture",
                                "Ready", "Implemented", "Validated", "Deprecated",
                            ],
                        },
                        "comment": {"type": "string"},
                    },
                    "required": ["requirement_id", "new_status"],
                },
            },
            {
                "name": "archive_requirement",
                "description": "Archive a requirement (soft delete)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string", "description": "Requirement ID (REQ-XXXX)"},
                    },
                    "required": ["requirement_id"],
                },
            },
            {
                "name": "query_requirements",
                "description": "Search and filter requirements",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "status": {"type": "string"},
                        "priority": {"type": "string"},
                        "type": {"type": "string"},
                        "search_text": {"type": "string"},
                        "include_archived": {
                            "type": "boolean",
                            "description": "Include archived requirements (default: false)",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["summary", "json", "markdown"],
                            "description": "Output format: summary (one-line per req), json (structured array), markdown (verbose table). Default: summary",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default: 25)",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of results to skip (default: 0)",
                        },
                    },
                },
            },
            {
                "name": "get_requirement_details",
                "description": "Get full requirement with all relationships. Use trace=true to include parent/child requirements.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string"},
                        "trace": {
                            "type": "boolean",
                            "description": "Include parent/child requirement hierarchy (default: false)",
                        },
                    },
                    "required": ["requirement_id"],
                },
            },
            {
                "name": "batch_create_requirements",
                "description": "Create multiple requirements atomically (all or nothing)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "requirements": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "type": {"type": "string", "enum": ["FUNC", "NFUNC", "TECH", "BUS", "INTF"]},
                                    "title": {"type": "string"},
                                    "priority": {"type": "string", "enum": ["P0", "P1", "P2", "P3"]},
                                    "current_state": {"type": "string"},
                                    "desired_state": {"type": "string"},
                                    "functional_requirements": {"type": "array", "items": {"type": "string"}},
                                    "nonfunctional_requirements": {"type": "array", "items": {"type": "string"}},
                                    "out_of_scope": {"type": "array", "items": {"type": "string"}},
                                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                                    "business_value": {"type": "string"},
                                    "author": {"type": "string"},
                                },
                                "required": ["type", "title", "priority"],
                            },
                            "description": "Array of requirement objects to create",
                        },
                    },
                    "required": ["project_id", "requirements"],
                },
            },
            {
                "name": "clone_requirement",
                "description": (
                    "Clone a requirement with a new ID. Copies relationships. "
                    "Resets status to Draft. Review copied relationships for applicability."
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "requirement_id": {"type": "string"},
                        "target_project_id": {
                            "type": "string",
                            "description": "Clone into a different project (default: same project)",
                        },
                    },
                    "required": ["requirement_id"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Route tool calls to handler methods"""
        handlers = {
            "create_requirement": self._create_requirement,
            "update_requirement": self._update_requirement,
            "update_requirement_status": self._update_requirement_status,
            "archive_requirement": self._archive_requirement,
            "query_requirements": self._query_requirements,
            "get_requirement_details": self._get_requirement_details,
            "batch_create_requirements": self._batch_create_requirements,
            "clone_requirement": self._clone_requirement,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._create_error_response(f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return self._create_error_response(f"Error in {tool_name}", exception=e)

    # ------------------------------------------------------------------
    # create_requirement
    # ------------------------------------------------------------------

    async def _create_requirement(self, params: dict[str, Any]) -> list[TextContent]:
        """Create a requirement linked to a project."""
        error = self._validate_required_params(params, ["project_id", "type", "title", "priority"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        req_id, _ = await self.db.generate_id("requirement")
        data = self._build_requirement_data(req_id, project_id, params)
        await self.db.insert_record("requirements", data)
        await self._log_operation("requirement", req_id, "created", project_id=project_id)

        key_info = f"Requirement {req_id} created"
        action_info = f"{params['title']} | {params['type']} | {params['priority']}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # update_requirement (broad planning update)
    # ------------------------------------------------------------------

    async def _update_requirement(self, params: dict[str, Any]) -> list[TextContent]:
        """Update requirement fields."""
        error = self._validate_required_params(params, ["requirement_id"])
        if error:
            return self._create_error_response(error)

        req_id = params["requirement_id"]
        error = await self._validate_not_archived("requirement", req_id)
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

        await self.db.update_record("requirements", data, "id = ?", [req_id])
        await self._log_operation("requirement", req_id, "updated")

        return self._create_above_fold_response(
            "SUCCESS",
            f"Requirement {req_id} updated",
            f"Updated fields: {', '.join(data.keys())}",
        )

    # ------------------------------------------------------------------
    # update_requirement_status
    # ------------------------------------------------------------------

    async def _update_requirement_status(self, params: dict[str, Any]) -> list[TextContent]:
        """Update requirement status with transition validation."""
        error = self._validate_required_params(params, ["requirement_id", "new_status"])
        if error:
            return self._create_error_response(error)

        req_id = params["requirement_id"]
        new_status = params["new_status"]

        # Get current status
        rows = await self.db.get_records("requirements", "status", where_clause="id = ?", where_params=[req_id])
        if not rows:
            return self._create_error_response(f"Requirement not found: {req_id}")

        current_status = rows[0]["status"]
        allowed = REQUIREMENT_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            return self._create_error_response(
                f"Invalid transition from '{current_status}' to '{new_status}'. "
                f"Allowed transitions: {allowed or 'none (terminal state)'}"
            )

        # Update status
        await self.db.update_record("requirements", {"status": new_status}, "id = ?", [req_id])

        # Add review comment if provided
        if params.get("comment"):
            await self._add_review_comment("requirement", req_id, params["comment"])

        key_info = f"Requirement {req_id} updated"
        action_info = f"{current_status} -> {new_status}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # archive_requirement
    # ------------------------------------------------------------------

    async def _archive_requirement(self, params: dict[str, Any]) -> list[TextContent]:
        """Archive a requirement (soft delete)."""
        error = self._validate_required_params(params, ["requirement_id"])
        if error:
            return self._create_error_response(error)

        req_id = params["requirement_id"]
        error = await self._validate_entity_exists("requirement", req_id)
        if error:
            return self._create_error_response(error)

        await self.db.execute_query(
            "UPDATE requirements SET is_archived = 1, archived_at = datetime('now') WHERE id = ?",
            [req_id],
        )
        await self._log_operation("requirement", req_id, "archived")

        return self._create_above_fold_response("SUCCESS", f"Requirement {req_id} archived")

    # ------------------------------------------------------------------
    # query_requirements
    # ------------------------------------------------------------------

    async def _query_requirements(self, params: dict[str, Any]) -> list[TextContent]:
        """Query requirements with filters, output format, and pagination."""
        conditions, query_params = self._build_query_filters(params)
        where_clause = " AND ".join(conditions) if conditions else ""

        output_format = params.get("output_format", "summary")
        limit = params.get("limit", 25)
        offset = params.get("offset", 0)

        # Build query with LIMIT/OFFSET
        columns = "*"
        query = f"SELECT {columns} FROM requirements"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += " ORDER BY priority, created_at DESC"
        query += " LIMIT ? OFFSET ?"
        query_params.extend([limit, offset])

        requirements = await self.db.execute_query(
            query, query_params, fetch_all=True, row_factory=True,
        )

        if not requirements:
            return self._create_above_fold_response("INFO", "No requirements found", "Try adjusting search criteria")

        filter_desc = self._build_filter_description(params)

        if output_format == "json":
            result_list = [
                {"id": req["id"], "title": req["title"], "status": req["status"], "priority": req["priority"]}
                for req in requirements
            ]
            return [TextContent(type="text", text=json.dumps(result_list))]

        if output_format == "markdown":
            lines = []
            for req in requirements:
                info = f"- {req['id']}: {req['title']} [{req['status']}] {req['priority']}"
                lines.append(info)
            key_info = self._format_count_summary("requirement", len(requirements), filter_desc)
            return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

        # Default: summary — one-line pipe-delimited format
        lines = []
        for req in requirements:
            lines.append(f"{req['id']} | {req['title']} | {req['status']} | {req['priority']}")
        key_info = self._format_count_summary("requirement", len(requirements), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

    # ------------------------------------------------------------------
    # get_requirement_details
    # ------------------------------------------------------------------

    async def _get_requirement_details(self, params: dict[str, Any]) -> list[TextContent]:
        """Get full requirement details with relationships. Optionally trace parent/child reqs."""
        error = self._validate_required_params(params, ["requirement_id"])
        if error:
            return self._create_error_response(error)

        req_id = params["requirement_id"]
        trace = params.get("trace", False)

        rows = await self.db.get_records("requirements", "*", where_clause="id = ?", where_params=[req_id])
        if not rows:
            return self._create_error_response(f"Requirement not found: {req_id}")

        req = dict(rows[0])

        # Build report
        report = f"""# Requirement Details: {req["id"]}

## Basic Information
- **Title**: {req["title"]}
- **Type**: {req["type"]}
- **Status**: {req["status"]}
- **Priority**: {req["priority"]}
- **Author**: {req.get("author") or "Not specified"}
- **Project**: {req["project_id"]}
- **Created**: {req["created_at"]}
- **Updated**: {req["updated_at"]}

## Problem Definition
**Current State**: {req.get("current_state") or "Not specified"}

**Desired State**: {req.get("desired_state") or "Not specified"}

**Business Value**: {req.get("business_value") or "Not specified"}
"""

        # JSON array fields
        if req.get("functional_requirements"):
            func_reqs = self._safe_json_loads(req["functional_requirements"])
            if func_reqs:
                report += "\n### Functional Requirements\n"
                for fr in func_reqs:
                    report += f"- {fr}\n"

        if req.get("nonfunctional_requirements"):
            nfunc_reqs = self._safe_json_loads(req["nonfunctional_requirements"])
            if nfunc_reqs:
                report += "\n### Non-Functional Requirements\n"
                for nfr in nfunc_reqs:
                    report += f"- {nfr}\n"

        if req.get("out_of_scope"):
            oos = self._safe_json_loads(req["out_of_scope"])
            if oos:
                report += "\n### Out of Scope\n"
                for item in oos:
                    report += f"- {item}\n"

        if req.get("acceptance_criteria"):
            acc_criteria = self._safe_json_loads(req["acceptance_criteria"])
            if acc_criteria:
                report += "\n### Acceptance Criteria\n"
                for ac in acc_criteria:
                    report += f"- {ac}\n"

        # Parent/child requirements (only when trace=true)
        if trace:
            parent_requirements = await self.db.execute_query(
                """
                SELECT r.* FROM requirements r
                JOIN relationships rel ON r.id = rel.target_id
                WHERE rel.source_id = ? AND rel.source_type = 'requirement'
                  AND rel.target_type = 'requirement' AND rel.relationship_type = 'parent'
                """,
                [req_id],
                fetch_all=True,
                row_factory=True,
            )
            if parent_requirements:
                report += f"\n## Parent Requirements ({len(parent_requirements)})\n"
                for parent in parent_requirements:
                    report += f"- {parent['id']}: {parent['title']} [{parent['status']}]\n"

            child_requirements = await self.db.execute_query(
                """
                SELECT r.* FROM requirements r
                JOIN relationships rel ON r.id = rel.source_id
                WHERE rel.target_id = ? AND rel.source_type = 'requirement'
                  AND rel.target_type = 'requirement' AND rel.relationship_type = 'parent'
                ORDER BY r.created_at
                """,
                [req_id],
                fetch_all=True,
                row_factory=True,
            )
            if child_requirements:
                report += f"\n## Child Requirements ({len(child_requirements)})\n"
                for i, child in enumerate(child_requirements, 1):
                    report += f"{i}. {child['id']}: {child['title']} [{child['status']}]\n"

        # Linked tasks via relationships (both directions)
        tasks = await self._get_linked_tasks(req_id)
        if tasks:
            report += f"\n## Linked Tasks ({len(tasks)})\n"
            for task in tasks:
                report += f"- {task['id']}: {task['title']} [{task['status']}]\n"

        # Linked ADRs via relationships (both directions)
        adrs = await self._get_linked_adrs(req_id)
        if adrs:
            report += f"\n## Linked Architecture Decisions ({len(adrs)})\n"
            for adr in adrs:
                report += f"- {adr['id']}: {adr['title']} [{adr['status']}]\n"

        key_info = self._format_status_summary("Requirement", req["id"], req["status"])
        action_info = f"{req['title']} | {req['priority']}"
        return self._create_above_fold_response("INFO", key_info, action_info, report)

    # ------------------------------------------------------------------
    # batch_create_requirements
    # ------------------------------------------------------------------

    async def _batch_create_requirements(self, params: dict[str, Any]) -> list[TextContent]:
        """Create multiple requirements atomically."""
        error = self._validate_required_params(params, ["project_id", "requirements"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        req_defs = params["requirements"]

        if not req_defs:
            return self._create_error_response("No requirements provided in batch")

        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        # Validate all requirements before creating any
        for i, req_def in enumerate(req_defs):
            if "title" not in req_def or not req_def["title"]:
                return self._create_error_response(
                    f"Requirement at index {i} is missing required field: title"
                )
            if "type" not in req_def or not req_def["type"]:
                return self._create_error_response(
                    f"Requirement at index {i} is missing required field: type"
                )
            if "priority" not in req_def or not req_def["priority"]:
                return self._create_error_response(
                    f"Requirement at index {i} is missing required field: priority"
                )

        # Create all requirements
        created_ids = []
        for req_def in req_defs:
            req_id, _ = await self.db.generate_id("requirement")
            data = self._build_requirement_data(req_id, project_id, req_def)
            await self.db.insert_record("requirements", data)
            created_ids.append(req_id)

        await self._log_operation(
            "requirement", f"batch:{len(created_ids)}", "batch_created", project_id=project_id
        )

        ids_str = ", ".join(created_ids)
        return self._create_above_fold_response(
            "SUCCESS",
            f"Created {len(created_ids)} requirements",
            ids_str,
        )

    # ------------------------------------------------------------------
    # clone_requirement
    # ------------------------------------------------------------------

    async def _clone_requirement(self, params: dict[str, Any]) -> list[TextContent]:
        """Clone a requirement with a new ID."""
        error = self._validate_required_params(params, ["requirement_id"])
        if error:
            return self._create_error_response(error)

        req_id = params["requirement_id"]
        target_project_id = params.get("target_project_id")

        # Get original requirement
        rows = await self.db.get_records("requirements", "*", where_clause="id = ?", where_params=[req_id])
        if not rows:
            return self._create_error_response(f"Requirement not found: {req_id}")

        original = dict(rows[0])

        # Validate target project if specified
        project_id = target_project_id or original["project_id"]
        if target_project_id:
            error = await self._validate_project_exists(target_project_id)
            if error:
                return self._create_error_response(error)

        # Generate new ID
        new_id, _ = await self.db.generate_id("requirement")

        # Build clone data - copy all content fields, reset status
        clone_data: dict[str, Any] = {
            "id": new_id,
            "project_id": project_id,
            "type": original["type"],
            "title": original["title"],
            "status": "Draft",
            "priority": original["priority"],
        }

        # Copy optional fields
        for field in ["current_state", "desired_state", "business_value", "author",
                       "functional_requirements", "nonfunctional_requirements",
                       "out_of_scope", "acceptance_criteria"]:
            if original.get(field) is not None:
                clone_data[field] = original[field]

        await self.db.insert_record("requirements", clone_data)

        # Copy relationships (where original requirement is the source)
        rels = await self.db.get_records(
            "relationships",
            where_clause="source_id = ? AND source_type = 'requirement'",
            where_params=[req_id],
        )
        for rel in rels:
            clone_rel_id = f"rel-{new_id}-{rel['target_id']}-{rel['relationship_type']}"
            await self.db.insert_record("relationships", {
                "id": clone_rel_id,
                "source_type": "requirement",
                "source_id": new_id,
                "target_type": rel["target_type"],
                "target_id": rel["target_id"],
                "relationship_type": rel["relationship_type"],
                "project_id": project_id,
            })

        await self._log_operation("requirement", new_id, "cloned", project_id=project_id)

        return self._create_above_fold_response(
            "SUCCESS",
            f"Requirement {new_id} cloned from {req_id}",
            f"{original['title']} | {original['type']} | {original['priority']}",
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_requirement_data(self, req_id: str, project_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Build a requirement data dict for INSERT."""
        data: dict[str, Any] = {
            "id": req_id,
            "project_id": project_id,
            "type": params["type"],
            "title": params["title"],
            "priority": params["priority"],
            "status": "Draft",
        }
        # Optional scalar fields
        for field in ["current_state", "desired_state", "business_value", "author"]:
            if field in params and params[field] is not None:
                data[field] = params[field]
        # Optional JSON array fields
        for field in ["functional_requirements", "nonfunctional_requirements",
                       "out_of_scope", "acceptance_criteria"]:
            if field in params and params[field] is not None:
                data[field] = self._safe_json_dumps(params[field])
        return data

    def _build_query_filters(self, params: dict[str, Any]) -> tuple[list[str], list[Any]]:
        """Build WHERE conditions for query_requirements."""
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
        if params.get("type"):
            conditions.append("type = ?")
            query_params.append(params["type"])
        if params.get("search_text"):
            conditions.append("(title LIKE ? OR desired_state LIKE ?)")
            search = f"%{params['search_text']}%"
            query_params.extend([search, search])

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
        if params.get("type"):
            filters.append(f"type: {params['type']}")
        if params.get("search_text"):
            filters.append(f"search: {params['search_text']}")
        return " | ".join(filters) if filters else "all requirements"

    async def _get_linked_tasks(self, req_id: str) -> list:
        """Get tasks linked to a requirement via relationships (both directions)."""
        return await self.db.execute_query(
            """
            SELECT DISTINCT t.* FROM tasks t
            JOIN relationships rel ON
                (rel.source_id = ? AND rel.target_id = t.id AND rel.target_type = 'task')
                OR (rel.target_id = ? AND rel.source_id = t.id AND rel.source_type = 'task')
            WHERE t.is_archived = 0
            """,
            [req_id, req_id],
            fetch_all=True,
            row_factory=True,
        )

    async def _get_linked_adrs(self, req_id: str) -> list:
        """Get architecture decisions linked to a requirement via relationships (both directions)."""
        return await self.db.execute_query(
            """
            SELECT DISTINCT a.* FROM architecture a
            JOIN relationships rel ON
                (rel.source_id = ? AND rel.target_id = a.id AND rel.target_type = 'architecture')
                OR (rel.target_id = ? AND rel.source_id = a.id AND rel.source_type = 'architecture')
            WHERE a.is_archived = 0
            """,
            [req_id, req_id],
            fetch_all=True,
            row_factory=True,
        )
