#!/usr/bin/env python3
"""
Pattern Handler for MCP Lifecycle Management Server

Handles architectural pattern management and ADR-pattern linkages:
- create_architectural_pattern — register named patterns with type classification
- link_adr_to_pattern — associate ADRs with patterns via establishes/follows/refines roles
- query_architectural_patterns — search/filter patterns with role breakdown counts
- get_architectural_overview — grouped markdown report of patterns and their linked ADRs
"""

import json
from typing import Any

from mcp.types import TextContent

from lifecycle_mcp.constants import PATTERN_TYPES

from .base_handler import BaseHandler


class PatternHandler(BaseHandler):
    """Handler for architectural pattern MCP tools."""

    _VALID_ROLES = ("establishes", "follows", "refines")

    def __init__(self, db_manager):
        super().__init__(db_manager)

    # ------------------------------------------------------------------
    # Tool definitions
    # ------------------------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "create_architectural_pattern",
                "description": "Create a named architectural pattern linked to a project",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "name": {"type": "string", "description": "Pattern name"},
                        "type": {
                            "type": "string",
                            "enum": sorted(PATTERN_TYPES),
                            "description": "Pattern classification type",
                        },
                        "description": {"type": "string", "description": "Optional description"},
                    },
                    "required": ["project_id", "name", "type"],
                },
            },
            {
                "name": "link_adr_to_pattern",
                "description": "Link an ADR to an architectural pattern with a role",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "adr_id": {"type": "string", "description": "Architecture decision ID (ADR-XXXX)"},
                        "pattern_id": {"type": "string", "description": "Pattern ID (PAT-XXXX)"},
                        "role": {
                            "type": "string",
                            "enum": list(self._VALID_ROLES),
                            "description": "Role of the ADR relative to the pattern (default: follows)",
                        },
                    },
                    "required": ["adr_id", "pattern_id"],
                },
            },
            {
                "name": "query_architectural_patterns",
                "description": "Search and filter architectural patterns with role breakdown counts",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string"},
                        "type": {"type": "string"},
                        "search_text": {"type": "string"},
                        "include_archived": {
                            "type": "boolean",
                            "description": "Include archived patterns (default: false)",
                        },
                        "output_format": {
                            "type": "string",
                            "enum": ["summary", "json", "markdown"],
                            "description": "Output format (default: summary)",
                        },
                        "limit": {"type": "integer", "description": "Max results (default: 25)"},
                        "offset": {"type": "integer", "description": "Skip N results (default: 0)"},
                    },
                },
            },
            {
                "name": "get_architectural_overview",
                "description": "Grouped markdown report of patterns and their linked ADRs",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": "string", "description": "Project ID (PROJ-XXXX)"},
                        "type": {"type": "string", "description": "Filter to a single pattern type"},
                        "include_followers": {
                            "type": "boolean",
                            "description": "Include ADRs with role 'follows' (default: false)",
                        },
                    },
                    "required": ["project_id"],
                },
            },
        ]

    # ------------------------------------------------------------------
    # Tool routing
    # ------------------------------------------------------------------

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        handlers = {
            "create_architectural_pattern": self._create_architectural_pattern,
            "link_adr_to_pattern": self._link_adr_to_pattern,
            "query_architectural_patterns": self._query_architectural_patterns,
            "get_architectural_overview": self._get_architectural_overview,
        }
        handler = handlers.get(tool_name)
        if not handler:
            return self._create_error_response(f"Unknown tool: {tool_name}")
        try:
            return await handler(arguments)
        except Exception as e:
            return self._create_error_response(f"Error in {tool_name}", exception=e)

    # ------------------------------------------------------------------
    # create_architectural_pattern
    # ------------------------------------------------------------------

    async def _create_architectural_pattern(self, params: dict[str, Any]) -> list[TextContent]:
        error = self._validate_required_params(params, ["project_id", "name", "type"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        pattern_type = params["type"]

        # Validate project
        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        # Validate type
        if pattern_type not in PATTERN_TYPES:
            return self._create_error_response(
                f"Invalid pattern type: '{pattern_type}'. "
                f"Valid types: {', '.join(sorted(PATTERN_TYPES))}"
            )

        pat_id, _ = await self.db.generate_id("architectural_pattern")

        data: dict[str, Any] = {
            "id": pat_id,
            "project_id": project_id,
            "name": params["name"],
            "type": pattern_type,
        }
        if params.get("description"):
            data["description"] = params["description"]

        await self.db.insert_record("architectural_patterns", data)
        await self._log_operation("architectural_pattern", pat_id, "created", project_id=project_id)

        key_info = f"Architectural pattern {pat_id} created"
        action_info = f"{params['name']} | {pattern_type}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # link_adr_to_pattern
    # ------------------------------------------------------------------

    async def _link_adr_to_pattern(self, params: dict[str, Any]) -> list[TextContent]:
        error = self._validate_required_params(params, ["adr_id", "pattern_id"])
        if error:
            return self._create_error_response(error)

        adr_id = params["adr_id"]
        pattern_id = params["pattern_id"]
        role = params.get("role", "follows")

        if role not in self._VALID_ROLES:
            return self._create_error_response(
                f"Invalid role: '{role}'. Valid roles: {', '.join(self._VALID_ROLES)}"
            )

        # Look up ADR
        adr_rows = await self.db.get_records(
            "architecture", "id, project_id",
            where_clause="id = ?", where_params=[adr_id],
        )
        if not adr_rows:
            return self._create_error_response(f"Architecture decision not found: {adr_id}")

        # Look up pattern
        pat_rows = await self.db.get_records(
            "architectural_patterns", "id, project_id",
            where_clause="id = ?", where_params=[pattern_id],
        )
        if not pat_rows:
            return self._create_error_response(f"Architectural pattern not found: {pattern_id}")

        # Cross-project check
        adr_project = adr_rows[0]["project_id"]
        pat_project = pat_rows[0]["project_id"]
        if adr_project != pat_project:
            return self._create_error_response(
                f"Cross-project link not allowed: {adr_id} belongs to {adr_project}, "
                f"{pattern_id} belongs to {pat_project}"
            )

        # Insert link — handle UNIQUE constraint violation
        try:
            await self.db.insert_record("adr_patterns", {
                "adr_id": adr_id,
                "pattern_id": pattern_id,
                "role": role,
            })
        except Exception as exc:
            if "unique" in str(exc).lower() or "constraint" in str(exc).lower():
                return self._create_error_response(
                    f"Duplicate link: {adr_id} is already linked to {pattern_id}"
                )
            raise

        key_info = f"Linked {adr_id} to {pattern_id}"
        action_info = f"Role: {role}"
        return self._create_above_fold_response("SUCCESS", key_info, action_info)

    # ------------------------------------------------------------------
    # query_architectural_patterns
    # ------------------------------------------------------------------

    async def _query_architectural_patterns(self, params: dict[str, Any]) -> list[TextContent]:
        conditions: list[str] = []
        query_params: list[Any] = []

        if not params.get("include_archived", False):
            conditions.append("p.is_archived = 0")

        if params.get("project_id"):
            conditions.append("p.project_id = ?")
            query_params.append(params["project_id"])

        if params.get("type"):
            conditions.append("p.type = ?")
            query_params.append(params["type"])

        if params.get("search_text"):
            conditions.append("(p.name LIKE ? OR p.description LIKE ?)")
            search = f"%{params['search_text']}%"
            query_params.extend([search, search])

        where_clause = " AND ".join(conditions) if conditions else "1=1"
        limit = int(params.get("limit", 25))
        offset = int(params.get("offset", 0))
        output_format = params.get("output_format", "summary")

        query = f"""
            SELECT
                p.id, p.name, p.type, p.description, p.project_id,
                p.is_archived, p.created_at, p.updated_at,
                COALESCE(SUM(CASE WHEN ap.role = 'establishes' THEN 1 ELSE 0 END), 0) AS establishing,
                COALESCE(SUM(CASE WHEN ap.role = 'follows' THEN 1 ELSE 0 END), 0) AS following,
                COALESCE(SUM(CASE WHEN ap.role = 'refines' THEN 1 ELSE 0 END), 0) AS refining
            FROM architectural_patterns p
            LEFT JOIN adr_patterns ap ON ap.pattern_id = p.id
            WHERE {where_clause}
            GROUP BY p.id
            ORDER BY p.created_at DESC
            LIMIT {limit} OFFSET {offset}
        """

        rows = await self.db.execute_query(query, query_params, fetch_all=True, row_factory=True)

        if not rows:
            return self._create_above_fold_response(
                "INFO", "No architectural patterns found", "Try adjusting search criteria"
            )

        if output_format == "json":
            return self._format_query_json(rows)
        elif output_format == "markdown":
            return self._format_query_markdown(rows, params)
        else:
            return self._format_query_summary(rows, params)

    def _format_query_summary(self, rows: list, params: dict[str, Any]) -> list[TextContent]:
        lines = []
        for r in rows:
            role_parts = []
            if r["establishing"]:
                role_parts.append(f"{r['establishing']} establishing")
            if r["following"]:
                role_parts.append(f"{r['following']} following")
            if r["refining"]:
                role_parts.append(f"{r['refining']} refining")
            role_str = ", ".join(role_parts) if role_parts else "no linked ADRs"
            lines.append(f"{r['id']} | {r['name']} | {r['type']} | {role_str}")

        filter_desc = self._build_pattern_filter_desc(params)
        key_info = self._format_count_summary("architectural pattern", len(rows), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

    def _format_query_json(self, rows: list) -> list[TextContent]:
        result = []
        for r in rows:
            result.append({
                "id": r["id"],
                "name": r["name"],
                "type": r["type"],
                "description": r["description"],
                "project_id": r["project_id"],
                "is_archived": r["is_archived"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "establishing": r["establishing"],
                "following": r["following"],
                "refining": r["refining"],
            })
        return [TextContent(type="text", text=json.dumps(result))]

    def _format_query_markdown(self, rows: list, params: dict[str, Any]) -> list[TextContent]:
        lines = []
        for r in rows:
            desc = f" -- {r['description']}" if r["description"] else ""
            lines.append(
                f"- **{r['id']}**: {r['name']} [{r['type']}]{desc} "
                f"({r['establishing']} establishing, {r['following']} following, {r['refining']} refining)"
            )

        filter_desc = self._build_pattern_filter_desc(params)
        key_info = self._format_count_summary("architectural pattern", len(rows), filter_desc)
        return self._create_above_fold_response("SUCCESS", key_info, "", "\n".join(lines))

    def _build_pattern_filter_desc(self, params: dict[str, Any]) -> str:
        filters = []
        if params.get("project_id"):
            filters.append(f"project: {params['project_id']}")
        if params.get("type"):
            filters.append(f"type: {params['type']}")
        if params.get("search_text"):
            filters.append(f"search: {params['search_text']}")
        return " | ".join(filters) if filters else "all patterns"

    # ------------------------------------------------------------------
    # get_architectural_overview
    # ------------------------------------------------------------------

    async def _get_architectural_overview(self, params: dict[str, Any]) -> list[TextContent]:
        error = self._validate_required_params(params, ["project_id"])
        if error:
            return self._create_error_response(error)

        project_id = params["project_id"]
        error = await self._validate_project_exists(project_id)
        if error:
            return self._create_error_response(error)

        type_filter = params.get("type")
        include_followers = params.get("include_followers", False)

        # Fetch patterns
        pat_conditions = ["p.project_id = ?", "p.is_archived = 0"]
        pat_params: list[Any] = [project_id]
        if type_filter:
            pat_conditions.append("p.type = ?")
            pat_params.append(type_filter)

        patterns = await self.db.execute_query(
            f"""SELECT p.id, p.name, p.type, p.description
                FROM architectural_patterns p
                WHERE {' AND '.join(pat_conditions)}
                ORDER BY p.type, p.name""",
            pat_params,
            fetch_all=True,
            row_factory=True,
        )

        # For each pattern, get linked ADRs (non-archived, non-deprecated)
        pattern_sections: list[str] = []
        # Track all ADR IDs that are linked to any pattern
        linked_adr_ids: set[str] = set()

        # Group patterns by type
        patterns_by_type: dict[str, list] = {}
        for pat in patterns:
            patterns_by_type.setdefault(pat["type"], []).append(pat)

        for ptype, pats in patterns_by_type.items():
            for pat in pats:
                # Query linked ADRs with roles
                adr_rows = await self.db.execute_query(
                    """SELECT a.id, a.title, a.status, a.decision, ap.role
                       FROM adr_patterns ap
                       JOIN architecture a ON a.id = ap.adr_id
                       WHERE ap.pattern_id = ?
                         AND a.is_archived = 0
                         AND a.status != 'Deprecated'
                       ORDER BY ap.role, a.id""",
                    [pat["id"]],
                    fetch_all=True,
                    row_factory=True,
                )

                for row in adr_rows:
                    linked_adr_ids.add(row["id"])

                # Group by role
                by_role: dict[str, list] = {"establishes": [], "refines": [], "follows": []}
                for row in adr_rows:
                    by_role.setdefault(row["role"], []).append(row)

                section_lines = []
                desc_line = f"Scope: {pat['description']}" if pat["description"] else ""
                section_lines.append(f"## {pat['name']}  [{pat['id']}]")
                if desc_line:
                    section_lines.append(desc_line)
                section_lines.append("")

                # Establishes first
                if by_role["establishes"]:
                    section_lines.append("Established by")
                    for adr in by_role["establishes"]:
                        decision_preview = (adr["decision"] or "")[:300]
                        section_lines.append(f"  {adr['id']}: {adr['title']} [{adr['status']}]")
                        if decision_preview:
                            section_lines.append(f"  > Decision: {decision_preview}")

                # Refines second
                if by_role["refines"]:
                    section_lines.append("")
                    section_lines.append("Refined by")
                    for adr in by_role["refines"]:
                        decision_preview = (adr["decision"] or "")[:300]
                        section_lines.append(f"  {adr['id']}: {adr['title']} [{adr['status']}]")
                        if decision_preview:
                            section_lines.append(f"  > Decision: {decision_preview}")

                # Follows third — only if include_followers
                if include_followers and by_role["follows"]:
                    section_lines.append("")
                    section_lines.append("Followed by")
                    for adr in by_role["follows"]:
                        decision_preview = (adr["decision"] or "")[:300]
                        section_lines.append(f"  {adr['id']}: {adr['title']} [{adr['status']}]")
                        if decision_preview:
                            section_lines.append(f"  > Decision: {decision_preview}")

                pattern_sections.append("\n".join(section_lines))

        # Build uncategorised section: ADRs with no adr_patterns link
        uncategorised_conditions = [
            "a.project_id = ?",
            "a.is_archived = 0",
            "a.status != 'Deprecated'",
            "NOT EXISTS (SELECT 1 FROM adr_patterns ap WHERE ap.adr_id = a.id)",
        ]
        uncat_adrs = await self.db.execute_query(
            f"""SELECT a.id, a.title, a.status, a.decision
                FROM architecture a
                WHERE {' AND '.join(uncategorised_conditions)}
                ORDER BY a.id""",
            [project_id],
            fetch_all=True,
            row_factory=True,
        )

        # Build the full report
        type_label = f" [{type_filter}]" if type_filter else ""
        header = f"# Architectural Overview -- {project_id}{type_label}"

        report_parts = [header, ""]
        if pattern_sections:
            report_parts.append("\n\n---\n\n".join(pattern_sections))
        else:
            report_parts.append("No architectural patterns found.")

        if uncat_adrs:
            report_parts.append("")
            report_parts.append("---")
            report_parts.append("")
            report_parts.append("## Uncategorised")
            for adr in uncat_adrs:
                decision_preview = (adr["decision"] or "")[:300]
                report_parts.append(f"  {adr['id']}: {adr['title']} [{adr['status']}]")
                if decision_preview:
                    report_parts.append(f"  > Decision: {decision_preview}")

        report = "\n".join(report_parts)

        pattern_count = len(patterns)
        adr_count = len(linked_adr_ids)
        uncat_count = len(uncat_adrs)
        key_info = f"Architectural overview for {project_id}"
        action_info = f"{pattern_count} pattern(s), {adr_count} linked ADR(s), {uncat_count} uncategorised"
        return self._create_above_fold_response("INFO", key_info, action_info, report)
