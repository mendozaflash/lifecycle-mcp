#!/usr/bin/env python3
"""
Relationship Handler for MCP Lifecycle Management Server
Handles all entity relationship operations (CRUD)
"""

import json
from typing import Any

from mcp.types import TextContent

from lifecycle_mcp.constants import VALID_RELATIONSHIP_COMBINATIONS

from .base_handler import BaseHandler


class RelationshipHandler(BaseHandler):
    """Handler for entity relationship operations"""

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return relationship tool definitions"""
        return [
            {
                "name": "create_relationship",
                "description": "Create relationship between entities",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string"},
                        "target_id": {"type": "string"},
                        "relationship_type": {
                            "type": "string",
                            "enum": [
                                "implements",  # task implements requirement
                                "addresses",   # architecture addresses requirement
                                "depends",     # entity depends on another
                                "blocks",      # entity blocks another
                                "informs",     # entity informs another
                                "requires",    # entity requires another
                                "parent",      # parent-child relationship
                                "refines",     # refines another entity
                                "conflicts",   # conflicts with another entity
                                "relates",     # generic relationship
                            ],
                        },
                        "project_id": {"type": "string", "description": "Project ID"},
                    },
                    "required": ["source_id", "target_id", "relationship_type", "project_id"],
                },
            },
            {
                "name": "delete_relationship",
                "description": "Delete relationship between entities",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string"},
                        "target_id": {"type": "string"},
                        "relationship_type": {"type": "string"},
                    },
                    "required": ["source_id", "target_id"],
                },
            },
            {
                "name": "query_relationships",
                "description": "Query relationships with filtering, pagination, and output format control",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "entity_id": {
                            "type": "string",
                            "description": "Filter to relationships involving this entity (as source or target)",
                        },
                        "relationship_type": {"type": "string"},
                        "project_id": {"type": "string", "description": "Filter by project"},
                        "output_format": {
                            "type": "string",
                            "enum": ["summary", "json"],
                            "default": "summary",
                            "description": "Output format: summary (human-readable) or json (structured)",
                        },
                        "limit": {
                            "type": "integer",
                            "default": 50,
                            "description": "Maximum number of relationships to return",
                        },
                        "offset": {
                            "type": "integer",
                            "default": 0,
                            "description": "Number of relationships to skip (for pagination)",
                        },
                    },
                },
            },
        ]

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Handle relationship tool calls"""
        try:
            if tool_name == "create_relationship":
                return await self._create_relationship(arguments)
            elif tool_name == "delete_relationship":
                return await self._delete_relationship(arguments)
            elif tool_name == "query_relationships":
                return await self._query_relationships(arguments)
            else:
                return self._create_error_response(f"Unknown tool: {tool_name}")

        except Exception as e:
            return self._create_error_response(f"Error in {tool_name}", e)

    async def _create_relationship(self, args: dict[str, Any]) -> list[TextContent]:
        """Create a relationship between two entities"""
        error = self._validate_required_params(args, ["source_id", "target_id", "relationship_type", "project_id"])
        if error:
            return self._create_error_response(error)

        source_id = args["source_id"]
        target_id = args["target_id"]
        rel_type = args["relationship_type"]
        project_id = args["project_id"]

        # Determine entity types from IDs
        source_type = self._get_entity_type(source_id)
        target_type = self._get_entity_type(target_id)

        if not source_type or not target_type:
            return self._create_error_response(f"Invalid entity IDs: {source_id}, {target_id}")

        # Validate source entity exists
        source_err = await self._validate_entity_exists(source_type, source_id)
        if source_err:
            return self._create_error_response(source_err)

        # Validate target entity exists
        target_err = await self._validate_entity_exists(target_type, target_id)
        if target_err:
            return self._create_error_response(target_err)

        # Validate relationship makes sense
        if not self._validate_relationship(source_type, target_type, rel_type):
            return self._create_error_response(
                f"Invalid relationship: {source_type} -> {target_type} ({rel_type})"
            )

        # Check if relationship already exists
        if await self._relationship_exists(source_id, target_id, rel_type):
            return self._create_error_response(
                f"Relationship already exists: {source_id} -> {target_id} ({rel_type})"
            )

        # Create the relationship in appropriate table
        success = await self._insert_relationship(source_id, target_id, source_type, target_type, rel_type, project_id)

        if success:
            # Log the operation
            await self._log_operation("relationship", f"{source_id}-{target_id}", "created", project_id=project_id)

            return self._create_above_fold_response(
                "SUCCESS",
                f"Relationship created: {source_id} -> {target_id}",
                f"Type: {rel_type}",
            )
        else:
            return self._create_error_response("Failed to create relationship")

    async def _delete_relationship(self, args: dict[str, Any]) -> list[TextContent]:
        """Delete a relationship between two entities"""
        error = self._validate_required_params(args, ["source_id", "target_id"])
        if error:
            return self._create_error_response(error)

        source_id = args["source_id"]
        target_id = args["target_id"]
        rel_type = args.get("relationship_type")

        # Determine entity types
        source_type = self._get_entity_type(source_id)
        target_type = self._get_entity_type(target_id)

        if not source_type or not target_type:
            return self._create_error_response(f"Invalid entity IDs: {source_id}, {target_id}")

        # Delete the relationship
        deleted_count = await self._delete_relationship_record(source_id, target_id, source_type, target_type, rel_type)

        if deleted_count > 0:
            # Log the operation
            await self._log_operation("relationship", f"{source_id}-{target_id}", "deleted")

            return self._create_above_fold_response(
                "SUCCESS",
                f"Deleted {deleted_count} relationship(s): {source_id} -> {target_id}",
            )
        else:
            return self._create_error_response(
                f"No relationship found between {source_id} and {target_id}"
            )

    async def _query_relationships(self, args: dict[str, Any]) -> list[TextContent]:
        """Query relationships with filtering, pagination, and output format support."""
        entity_id = args.get("entity_id")
        rel_type = args.get("relationship_type")
        project_id = args.get("project_id")
        output_format = args.get("output_format", "summary")
        limit = args.get("limit", 50)
        offset = args.get("offset", 0)

        relationships = await self._fetch_all_relationships(
            project_id=project_id,
            entity_id=entity_id,
            relationship_type=rel_type,
            limit=limit,
            offset=offset,
        )

        if entity_id:
            key_info = f"Found {len(relationships)} relationship(s) for {entity_id}"
        else:
            key_info = f"Found {len(relationships)} relationship(s) of type {rel_type or 'all'}"

        if output_format == "json":
            details = self._format_relationships_json(relationships)
        else:
            details = self._format_relationships_summary(relationships)

        return self._create_above_fold_response("SUCCESS", key_info, "", details)

    def _get_entity_type(self, entity_id: str) -> str | None:
        """Determine entity type from ID prefix"""
        if entity_id.startswith("REQ-"):
            return "requirement"
        elif entity_id.startswith("TASK-"):
            return "task"
        elif entity_id.startswith("ADR-"):
            return "architecture"
        elif entity_id.startswith("PROJ-"):
            return "project"
        return None

    def _validate_relationship(self, source_type: str, target_type: str, rel_type: str) -> bool:
        """Validate that relationship type is valid for entity types"""
        return (source_type, target_type, rel_type) in VALID_RELATIONSHIP_COMBINATIONS

    async def _relationship_exists(self, source_id: str, target_id: str, rel_type: str) -> bool:
        """Check if relationship already exists in unified relationships table"""
        source_type = self._get_entity_type(source_id)
        target_type = self._get_entity_type(target_id)

        if not source_type or not target_type:
            return False

        # Check unified relationships table
        results = await self.db.get_records(
            "relationships",
            "1",
            "source_type = ? AND source_id = ? AND target_type = ? AND target_id = ? AND relationship_type = ?",
            [source_type, source_id, target_type, target_id, rel_type]
        )
        return len(results) > 0

    async def _insert_relationship(self, source_id: str, target_id: str, source_type: str, target_type: str, rel_type: str, project_id: str | None = None) -> bool:
        """Insert relationship into unified relationships table"""
        try:
            # Generate unique relationship ID
            relationship_id = f"rel-{source_id}-{target_id}-{rel_type}"

            data: dict[str, Any] = {
                "id": relationship_id,
                "source_type": source_type,
                "source_id": source_id,
                "target_type": target_type,
                "target_id": target_id,
                "relationship_type": rel_type,
            }
            if project_id is not None:
                data["project_id"] = project_id

            # Insert into unified relationships table
            await self.db.insert_record("relationships", data)

            return True
        except Exception as e:
            self.logger.error(f"Failed to insert relationship: {str(e)}")
            return False

    async def _delete_relationship_record(self, source_id: str, target_id: str, source_type: str, target_type: str, rel_type: str | None = None) -> int:
        """Delete relationship record from unified relationships table and return count of deleted records"""
        try:
            if not source_type or not target_type:
                return 0

            # Build WHERE clause for unified relationships table
            if rel_type:
                where_clause = "source_type = ? AND source_id = ? AND target_type = ? AND target_id = ? AND relationship_type = ?"
                params = [source_type, source_id, target_type, target_id, rel_type]
            else:
                where_clause = "source_type = ? AND source_id = ? AND target_type = ? AND target_id = ?"
                params = [source_type, source_id, target_type, target_id]

            # Get count before deletion for return value
            existing = await self.db.get_records("relationships", "COUNT(*) as count", where_clause, params)
            count = existing[0]["count"] if existing else 0

            if count > 0:
                await self.db.delete_record("relationships", where_clause, params)
                return count

            return 0

        except Exception as e:
            self.logger.error(f"Failed to delete relationship: {str(e)}")
            return 0

    async def _fetch_all_relationships(
        self,
        project_id: str | None = None,
        entity_id: str | None = None,
        relationship_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch relationships using a single JOIN query (no N+1 per-row lookups).

        Resolves source/target titles via LEFT JOINs against requirements, tasks,
        and architecture tables. Falls back to the entity ID if no title is found.
        """
        query = """
            SELECT r.source_id, r.target_id, r.source_type, r.target_type,
                   r.relationship_type,
                   COALESCE(req_s.title, task_s.title, adr_s.title) AS source_title,
                   COALESCE(req_t.title, task_t.title, adr_t.title) AS target_title
            FROM relationships r
            LEFT JOIN requirements req_s ON r.source_id = req_s.id AND r.source_type = 'requirement'
            LEFT JOIN tasks task_s       ON r.source_id = task_s.id AND r.source_type = 'task'
            LEFT JOIN architecture adr_s ON r.source_id = adr_s.id AND r.source_type = 'architecture'
            LEFT JOIN requirements req_t ON r.target_id = req_t.id AND r.target_type = 'requirement'
            LEFT JOIN tasks task_t       ON r.target_id = task_t.id AND r.target_type = 'task'
            LEFT JOIN architecture adr_t ON r.target_id = adr_t.id AND r.target_type = 'architecture'
        """

        conditions: list[str] = []
        params: list[Any] = []

        if project_id:
            conditions.append("r.project_id = ?")
            params.append(project_id)

        if entity_id:
            conditions.append("(r.source_id = ? OR r.target_id = ?)")
            params.extend([entity_id, entity_id])

        if relationship_type:
            conditions.append("r.relationship_type = ?")
            params.append(relationship_type)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY r.rowid"
        query += " LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self.db.execute_query(query, params, fetch_all=True, row_factory=True)

        relationships: list[dict[str, Any]] = []
        for row in rows:
            relationships.append({
                "source_id": row["source_id"],
                "target_id": row["target_id"],
                "type": row["relationship_type"],
                "source_title": row["source_title"] or row["source_id"],
                "target_title": row["target_title"] or row["target_id"],
            })

        return relationships

    def _format_relationships_summary(self, relationships: list[dict[str, Any]]) -> str:
        """Format relationships as one-line-per-relationship summary."""
        if not relationships:
            return "No relationships found."

        lines = ["# Relationships\n"]
        for rel in relationships:
            source_title = rel.get("source_title", rel["source_id"])
            target_title = rel.get("target_title", rel["target_id"])
            rel_type = rel["type"]

            lines.append(f"- **{source_title}** ({rel['source_id']}) -> **{target_title}** ({rel['target_id']}) [{rel_type}]")

        return "\n".join(lines)

    def _format_relationships_json(self, relationships: list[dict[str, Any]]) -> str:
        """Format relationships as a JSON array."""
        simplified = []
        for rel in relationships:
            simplified.append({
                "source_id": rel["source_id"],
                "target_id": rel["target_id"],
                "relationship_type": rel["type"],
                "source_title": rel.get("source_title", ""),
                "target_title": rel.get("target_title", ""),
            })

        return f"```json\n{json.dumps(simplified, indent=2)}\n```"
