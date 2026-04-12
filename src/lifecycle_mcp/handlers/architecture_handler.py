#!/usr/bin/env python3
"""
Architecture Handler for MCP Lifecycle Management Server (v2)

Handles all architecture decision-related operations using the v2 schema:
- Sequential IDs via generate_id("architecture")
- Project-scoped ADRs (project_id FK)
- Polymorphic relationships table
- No type/decision_outcome columns -- uses decision column
- Supersession via superseded_by FK + Deprecated status (no Superseded status)
- Archive (soft delete) support
"""

import json
from typing import Any

from mcp.types import TextContent

from lifecycle_mcp.constants import ARCHITECTURE_TRANSITIONS

from .base_handler import BaseHandler


class ArchitectureHandler(BaseHandler):
    """Handler for architecture decision-related MCP tools (v2 schema)"""

    # Fields that may be updated via update_architecture_decision
    _UPDATABLE_FIELDS = ["title", "context", "decision"]
    _UPDATABLE_JSON_FIELDS = ["decision_drivers", "considered_options", "consequences", "authors"]

    def __init__(self, db_manager):
        """Initialize handler with database manager"""
        super().__init__(db_manager)

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return architecture tool definitions"""
        return [
            {
                "name": "create_architecture_decision",
                "description": "Record architecture decision (ADR) linked to a project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "title": {"type": "string"},
                        "context": {"type": "string"},
                        "decision": {"type": "string"},
                        "decision_drivers": {"type": "array", "items": {"type": "string"}},
                        "considered_options": {"type": "array", "items": {"type": "string"}},
                        "consequences": {"type": "object"},
                        "authors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["project_id", "title", "context", "decision"],
                },
            },
            {
                "name": "update_architecture_decision",
                "description": "Update architecture decision fields (title, context, decision, etc.)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "architecture_id": {"type": "string"},
                        "title": {"type": "string"},
                        "context": {"type": "string"},
                        "decision": {"type": "string"},
                        "decision_drivers": {"type": "array", "items": {"type": "string"}},
                        "considered_options": {"type": "array", "items": {"type": "string"}},
                        "consequences": {"type": "object"},
                        "authors": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["architecture_id"],
                },
            },
            {
                "name": "update_architecture_status",
                "description": "Update architecture decision status with transition validation. "
                "Supports shortcut transition: Under Review->Accepted.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "architecture_id": {"type": "string"},
                        "new_status": {
                            "type": "string",
                            "enum": [
                                "Under Review", "Proposed", "Accepted",
                                "Rejected", "Deprecated",
                            ],
                        },
                        "comment": {"type": "string"},
                        "superseded_by": {
                            "type": "string",
                            "description": "ADR ID that supersedes this one (used with Deprecated status)",
                        },
                    },
                    "required": ["architecture_id", "new_status"],
                },
            },
            {
                "name": "archive_architecture_decision",
                "description": "Archive an architecture decision (soft delete)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "architecture_id": {"type": "string", "description": "ADR ID (ADR-XXXX)"},
                    },
                    "required": ["architecture_id"],
                },
            },
            {
                "name": "query_architecture_decisions",
                "description": "Search and filter architecture decisions",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "status": {"type": "string"},
                        "search_text": {"type": "string"},
                        "include_archived": {
                            "type": "boolean",
                            "description": "Include archived decisions (default: false)",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["summary", "json", "markdown"],
                            "description": "Output format: summary (one-line per ADR), json (structured array), markdown (verbose). Default: summary",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results to return (default: 25)",
                        },
                        "offset": {
                            "type": "integer",
                            "description": "Number of results to skip for pagination (default: 0)",
                        },
                    },
                },
            },
            {
                "name": "get_architecture_details",
                "description": "Get full architecture decision details with relationships and reviews",
                "inputSchema": {
                    "type": "object",
                    "properties": {"architecture_id": {"type": "string"}},
                    "required": ["architecture_id"],
                },
            },
            {
                "name": "add_architecture_review",
                "description": "Add review comment to architecture decision",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "architecture_id": {"type": "string"},
                        "comment": {"type": "string"},
                        "reviewer": {"type": "string"},
                    },
                    "required": ["architecture_id", "comment"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Route tool calls to handler methods"""
        handlers = {
            "create_architecture_decision": self._create_architecture_decision,
            "update_architecture_decision": self._update_architecture_decision,
            "update_architecture_status": self._update_architecture_status,
            "archive_architecture_decision": self._archive_architecture_decision,
            "query_architecture_decisions": self._query_architecture_decisions,
            "get_architecture_details": self._get_architecture_details,
            "add_architecture_review": self._add_architecture_review,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._create_error_response(f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return self._create_error_response(f"Error in {tool_name}", exception=e)

    # ------------------------------------------------------------------
    # create_architecture_decision
    # ------------------------------------------------------------------

    async def _create_architecture_decision(self, params: dict[str, Any]) -> list[TextContent]:
        """Create an ADR linked to a project."""
        error = self._validate_required_params(params, ["project_id", "title", "context", "decision"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        adr_id, _ = await self.db.generate_id("architecture")

        data: dict[str, Any] = {
            "id": adr_id,
            "project_id": project_id,
            "title": params["title"],
            "context": params["context"],
            "decision": params["decision"],
            "status": "Under Review",
        }

        # Optional JSON fields
        for field in self._UPDATABLE_JSON_FIELDS:
            if field in params and params[field] is not None:
                data[field] = self._safe_json_dumps(params[field])

        await self.db.insert_record("architecture", data)
        await self._log_operation("architecture", adr_id, "created", project_id=project_id)

        key_info = f"Architecture decision {adr_id} created"
        action_info = f"{params['title']} | Under Review"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # update_architecture_decision (broad field update)
    # ------------------------------------------------------------------

    async def _update_architecture_decision(self, params: dict[str, Any]) -> list[TextContent]:
        """Update architecture decision fields."""
        error = self._validate_required_params(params, ["architecture_id"])
        if error:
            return self._create_error_response(error)

        adr_id = params["architecture_id"]
        error = await self._validate_not_archived("architecture", adr_id)
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

        await self.db.update_record("architecture", data, "id = ?", [adr_id])
        await self._log_operation("architecture", adr_id, "updated")

        return self._create_above_fold_response(
            "SUCCESS",
            f"Architecture decision {adr_id} updated",
            f"Updated fields: {', '.join(data.keys())}",
        )

    # ------------------------------------------------------------------
    # update_architecture_status
    # ------------------------------------------------------------------

    async def _update_architecture_status(self, params: dict[str, Any]) -> list[TextContent]:
        """Update architecture decision status with transition validation."""
        error = self._validate_required_params(params, ["architecture_id", "new_status"])
        if error:
            return self._create_error_response(error)

        adr_id = params["architecture_id"]
        new_status = params["new_status"]

        # Get current status
        rows = await self.db.get_records(
            "architecture", "status", where_clause="id = ?", where_params=[adr_id]
        )
        if not rows:
            return self._create_error_response(f"Architecture decision not found: {adr_id}")

        current_status = rows[0]["status"]
        allowed = ARCHITECTURE_TRANSITIONS.get(current_status, [])
        if new_status not in allowed:
            return self._create_error_response(
                f"Invalid transition from '{current_status}' to '{new_status}'. "
                f"Allowed transitions: {allowed or 'none (terminal state)'}"
            )

        # Build update data
        update_data: dict[str, Any] = {"status": new_status}

        # Handle superseded_by when setting to Deprecated
        if new_status == "Deprecated" and params.get("superseded_by"):
            update_data["superseded_by"] = params["superseded_by"]

        await self.db.update_record("architecture", update_data, "id = ?", [adr_id])

        # Add review comment if provided
        if params.get("comment"):
            await self._add_review_comment("architecture", adr_id, params["comment"])

        key_info = f"Architecture {adr_id} updated"
        action_info = f"{current_status} -> {new_status}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # archive_architecture_decision
    # ------------------------------------------------------------------

    async def _archive_architecture_decision(self, params: dict[str, Any]) -> list[TextContent]:
        """Archive an architecture decision (soft delete)."""
        error = self._validate_required_params(params, ["architecture_id"])
        if error:
            return self._create_error_response(error)

        adr_id = params["architecture_id"]
        error = await self._validate_entity_exists("architecture", adr_id)
        if error:
            return self._create_error_response(error)

        await self.db.execute_query(
            "UPDATE architecture SET is_archived = 1, archived_at = datetime('now') WHERE id = ?",
            [adr_id],
        )
        await self._log_operation("architecture", adr_id, "archived")

        return self._create_above_fold_response("SUCCESS", f"Architecture decision {adr_id} archived")

    # ------------------------------------------------------------------
    # query_architecture_decisions
    # ------------------------------------------------------------------

    async def _query_architecture_decisions(self, params: dict[str, Any]) -> list[TextContent]:
        """Query architecture decisions with filters and configurable output format."""
        conditions, query_params = self._build_query_filters(params)
        where_clause = " AND ".join(conditions) if conditions else ""

        limit = params.get("limit", 25)
        offset = params.get("offset", 0)
        output_format = params.get("output_format", "summary")

        # Build SQL with LIMIT/OFFSET
        query = "SELECT * FROM architecture"
        if where_clause:
            query += f" WHERE {where_clause}"
        query += " ORDER BY created_at DESC"
        query += f" LIMIT {int(limit)} OFFSET {int(offset)}"

        decisions = await self.db.execute_query(
            query, query_params, fetch_all=True, row_factory=True,
        )

        if not decisions:
            return self._create_above_fold_response(
                "INFO", "No architecture decisions found", "Try adjusting search criteria"
            )

        if output_format == "json":
            return self._format_json_output(decisions)
        elif output_format == "markdown":
            return self._format_markdown_output(decisions, params)
        else:  # summary (default)
            return self._format_summary_output(decisions, params)

    # ------------------------------------------------------------------
    # get_architecture_details
    # ------------------------------------------------------------------

    async def _get_architecture_details(self, params: dict[str, Any]) -> list[TextContent]:
        """Get full architecture decision details with relationships and reviews."""
        error = self._validate_required_params(params, ["architecture_id"])
        if error:
            return self._create_error_response(error)

        adr_id = params["architecture_id"]
        rows = await self.db.get_records("architecture", "*", where_clause="id = ?", where_params=[adr_id])
        if not rows:
            return self._create_error_response(f"Architecture decision not found: {adr_id}")

        arch = dict(rows[0])

        # Build detailed report
        report = f"""# Architecture Decision: {arch["id"]}

## Basic Information
- **Title**: {arch["title"]}
- **Status**: {arch["status"]}
- **Project**: {arch["project_id"]}
- **Created**: {arch["created_at"]}
- **Updated**: {arch["updated_at"]}
- **Authors**: {arch.get("authors") or "Not specified"}

## Context
{arch.get("context") or "Not specified"}

## Decision
{arch.get("decision") or "Not specified"}
"""

        if arch.get("decision_drivers"):
            drivers = self._safe_json_loads(arch["decision_drivers"])
            if drivers:
                report += "\n## Decision Drivers\n"
                for driver in drivers:
                    report += f"- {driver}\n"

        if arch.get("considered_options"):
            options = self._safe_json_loads(arch["considered_options"])
            if options:
                report += "\n## Considered Options\n"
                for option in options:
                    report += f"- {option}\n"

        if arch.get("consequences"):
            consequences = self._safe_json_loads(arch["consequences"])
            if consequences:
                report += "\n## Consequences\n"
                if isinstance(consequences, dict):
                    for key, value in consequences.items():
                        report += f"**{key.title()}**: {value}\n"
                else:
                    report += f"{consequences}\n"

        # Superseding ADR info
        if arch.get("superseded_by"):
            superseding = await self.db.get_records(
                "architecture", "id, title, status",
                where_clause="id = ?",
                where_params=[arch["superseded_by"]],
            )
            if superseding:
                s = superseding[0]
                report += "\n## Superseded By\n"
                report += f"- {s['id']}: {s['title']} [{s['status']}]\n"
            else:
                report += "\n## Superseded By\n"
                report += f"- {arch['superseded_by']} (details unavailable)\n"

        # Get linked requirements via relationships (both directions)
        requirements = await self.db.execute_query(
            """
            SELECT DISTINCT r.* FROM requirements r
            JOIN relationships rel ON
                (rel.source_id = ? AND rel.target_id = r.id AND rel.target_type = 'requirement')
                OR (rel.target_id = ? AND rel.source_id = r.id AND rel.source_type = 'requirement')
            WHERE r.is_archived = 0
            """,
            [adr_id, adr_id],
            fetch_all=True,
            row_factory=True,
        )

        if requirements:
            report += f"\n## Linked Requirements ({len(requirements)})\n"
            for req in requirements:
                report += f"- {req['id']}: {req['title']}\n"

        # Get reviews
        reviews = await self.db.execute_query(
            """
            SELECT reviewer, comment, created_at FROM reviews
            WHERE entity_type = 'architecture' AND entity_id = ?
            ORDER BY created_at DESC
            """,
            [adr_id],
            fetch_all=True,
            row_factory=True,
        )

        if reviews:
            report += f"\n## Reviews ({len(reviews)})\n"
            for review in reviews:
                report += f"- **{review['reviewer']}** ({review['created_at']}): {review['comment']}\n"

        key_info = f"Architecture {arch['id']} details"
        action_info = f"{arch['title']} | {arch['status']}"
        return self._create_above_fold_response("INFO", key_info, action_info, report)

    # ------------------------------------------------------------------
    # add_architecture_review
    # ------------------------------------------------------------------

    async def _add_architecture_review(self, params: dict[str, Any]) -> list[TextContent]:
        """Add review comment to architecture decision."""
        error = self._validate_required_params(params, ["architecture_id", "comment"])
        if error:
            return self._create_error_response(error)

        adr_id = params["architecture_id"]
        error = await self._validate_entity_exists("architecture", adr_id)
        if error:
            return self._create_error_response(error)

        reviewer = params.get("reviewer", "MCP User")
        await self._add_review_comment("architecture", adr_id, params["comment"], reviewer)

        key_info = f"Review added to {adr_id}"
        action_info = f"Review by {reviewer}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_query_filters(self, params: dict[str, Any]) -> tuple[list[str], list[Any]]:
        """Build WHERE conditions for query methods."""
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
        if params.get("search_text"):
            conditions.append("(title LIKE ? OR context LIKE ?)")
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
        if params.get("search_text"):
            filters.append(f"search: {params['search_text']}")
        return " | ".join(filters) if filters else "all decisions"

    # ------------------------------------------------------------------
    # Output format helpers
    # ------------------------------------------------------------------

    def _format_summary_output(self, decisions: list, params: dict[str, Any]) -> list[TextContent]:
        """Format decisions as one-line-per-ADR summary."""
        lines = []
        for d in decisions:
            lines.append(f"{d['id']} | {d['title']} | {d['status']}")

        filter_desc = self._build_filter_description(params)
        key_info = self._format_count_summary("architecture decision", len(decisions), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

    def _format_json_output(self, decisions: list) -> list[TextContent]:
        """Format decisions as a JSON array with parsed JSON fields."""
        json_fields = ["decision_drivers", "considered_options", "consequences", "authors"]
        result_list = []
        for decision in decisions:
            decision_dict = dict(decision) if hasattr(decision, "keys") else decision
            for field in json_fields:
                if field in decision_dict and isinstance(decision_dict[field], str):
                    try:
                        decision_dict[field] = json.loads(decision_dict[field]) if decision_dict[field] else []
                    except (json.JSONDecodeError, TypeError):
                        decision_dict[field] = []
            result_list.append(decision_dict)

        return [TextContent(type="text", text=json.dumps(result_list))]

    def _format_markdown_output(self, decisions: list, params: dict[str, Any]) -> list[TextContent]:
        """Format decisions as verbose markdown."""
        lines = []
        for d in decisions:
            lines.append(f"- **{d['id']}**: {d['title']} [{d['status']}]")

        filter_desc = self._build_filter_description(params)
        key_info = self._format_count_summary("architecture decision", len(decisions), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))
