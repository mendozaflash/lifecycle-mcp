#!/usr/bin/env python3
"""
Export Handler for MCP Lifecycle Management Server (v2)

Handles export and diagram generation operations with project scoping.
All queries are scoped to a project_id and use the polymorphic
relationships table (no legacy join tables).
"""

import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from mcp.types import TextContent

from .base_handler import BaseHandler


class ExportHandler(BaseHandler):
    """Handler for export and diagram generation MCP tools"""

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return export tool definitions"""
        return [
            {
                "name": "export_project_documentation",
                "description": "Export comprehensive project documentation in markdown format",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (PROJ-XXXX)",
                        },
                        "include_requirements": {"type": "boolean", "default": True},
                        "include_tasks": {"type": "boolean", "default": True},
                        "include_architecture": {"type": "boolean", "default": True},
                        "output_directory": {
                            "type": "string",
                            "description": "Directory to save the exported files",
                        },
                    },
                    "required": ["project_id", "output_directory"],
                },
            },
            {
                "name": "create_architectural_diagrams",
                "description": "Generate Mermaid diagrams for project architecture and relationships",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Project ID (PROJ-XXXX)",
                        },
                        "diagram_type": {
                            "type": "string",
                            "enum": [
                                "requirements",
                                "tasks",
                                "architecture",
                                "full_project",
                                "directory_structure",
                                "dependencies",
                            ],
                        },
                        "requirement_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Specific requirements to include",
                        },
                        "include_relationships": {"type": "boolean", "default": True},
                        "output_format": {
                            "type": "string",
                            "enum": ["mermaid", "markdown_with_mermaid"],
                            "default": "mermaid",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Directory path to save diagram files",
                        },
                    },
                    "required": ["project_id", "output_path"],
                },
            },
        ]

    async def handle_tool_call(self, tool_name: str, arguments: dict[str, Any]) -> list[TextContent]:
        """Route tool calls to appropriate handler methods"""
        try:
            if tool_name == "export_project_documentation":
                return await self._export_project_documentation(**arguments)
            elif tool_name == "create_architectural_diagrams":
                return await self._create_architectural_diagrams(**arguments)
            else:
                return self._create_error_response(f"Unknown tool: {tool_name}")
        except Exception as e:
            return self._create_error_response(f"Error handling {tool_name}", e)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _get_project_name(self, project_id: str) -> str | None:
        """Look up project name from the projects table. Returns None if not found."""
        row = await self.db.execute_query(
            "SELECT name FROM projects WHERE id = ? AND is_archived = 0",
            [project_id],
            fetch_one=True,
            row_factory=True,
        )
        return row["name"] if row else None

    # ------------------------------------------------------------------
    # export_project_documentation
    # ------------------------------------------------------------------

    async def _export_project_documentation(self, **params) -> list[TextContent]:
        """Export comprehensive project documentation in markdown format"""
        try:
            project_id = params.get("project_id")
            if not project_id:
                return self._create_error_response("Missing required parameter: project_id")

            # Validate project exists
            project_name = await self._get_project_name(project_id)
            if project_name is None:
                return self._create_error_response(f"Project not found: {project_id}")

            output_dir = params.get("output_directory")
            if not output_dir:
                return self._create_error_response("Missing required parameter: output_directory")

            # Create output directory if needed
            os.makedirs(output_dir, exist_ok=True)

            exported_files = []

            if params.get("include_requirements", True):
                exported_files.extend(
                    await self._export_requirements(project_id, project_name, output_dir)
                )

            if params.get("include_tasks", True):
                exported_files.extend(
                    await self._export_tasks(project_id, project_name, output_dir)
                )

            if params.get("include_architecture", True):
                exported_files.extend(
                    await self._export_architecture(project_id, project_name, output_dir)
                )

            if exported_files:
                key_info = f"Exported {len(exported_files)} files to {output_dir}"
                action_info = f"{project_name} documentation"
                details = "\n".join(f"- {f}" for f in exported_files)
                return self._create_above_fold_response("SUCCESS", key_info, action_info, details)
            else:
                return self._create_above_fold_response(
                    "INFO",
                    "No data found to export",
                    "Check if requirements, tasks, or architecture exist",
                )

        except Exception as e:
            return self._create_error_response("Failed to export project documentation", e)

    async def _export_requirements(
        self, project_id: str, project_name: str, output_dir: str
    ) -> list[str]:
        """Export requirements to markdown file, scoped to project."""
        requirements = await self.db.get_records(
            "requirements",
            "*",
            where_clause="project_id = ? AND is_archived = 0",
            where_params=[project_id],
            order_by="type, id",
        )

        if not requirements:
            return []

        filename = f"{project_name}-requirements.md"
        filepath = os.path.join(output_dir, filename)

        content = f"# {project_name} - Requirements Documentation\n\n"
        content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # Group by type
        req_by_type: dict[str, list] = {}
        for req in requirements:
            req_type = req["type"]
            if req_type not in req_by_type:
                req_by_type[req_type] = []
            req_by_type[req_type].append(req)

        for req_type, reqs in req_by_type.items():
            content += f"## {req_type} Requirements\n\n"
            for req in reqs:
                content += f"### {req['id']}: {req['title']}\n\n"
                content += f"- **Status**: {req['status']}\n"
                content += f"- **Priority**: {req['priority']}\n"
                if req["author"]:
                    content += f"- **Author**: {req['author']}\n"
                content += f"- **Created**: {req['created_at']}\n"
                content += f"- **Updated**: {req['updated_at']}\n\n"

                if req["current_state"]:
                    content += f"**Current State**: {req['current_state']}\n\n"
                if req["desired_state"]:
                    content += f"**Desired State**: {req['desired_state']}\n\n"
                if req["business_value"]:
                    content += f"**Business Value**: {req['business_value']}\n\n"

                if req["functional_requirements"]:
                    func_reqs = self._safe_json_loads(req["functional_requirements"])
                    if func_reqs:
                        content += "**Functional Requirements**:\n"
                        for fr in func_reqs:
                            content += f"- {fr}\n"
                        content += "\n"

                if req["acceptance_criteria"]:
                    acc = self._safe_json_loads(req["acceptance_criteria"])
                    if acc:
                        content += "**Acceptance Criteria**:\n"
                        for ac in acc:
                            content += f"- {ac}\n"
                        content += "\n"

                content += "---\n\n"

        await asyncio.to_thread(Path(filepath).write_text, content, encoding="utf-8")

        return [filename]

    async def _export_tasks(
        self, project_id: str, project_name: str, output_dir: str
    ) -> list[str]:
        """Export tasks to markdown file, scoped to project."""
        tasks = await self.db.get_records(
            "tasks",
            "*",
            where_clause="project_id = ? AND is_archived = 0",
            where_params=[project_id],
            order_by="id",
        )

        if not tasks:
            return []

        filename = f"{project_name}-tasks.md"
        filepath = os.path.join(output_dir, filename)

        content = f"# {project_name} - Tasks Documentation\n\n"
        content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        # Group by status
        tasks_by_status: dict[str, list] = {}
        for task in tasks:
            status = task["status"]
            if status not in tasks_by_status:
                tasks_by_status[status] = []
            tasks_by_status[status].append(task)

        for status, task_list in tasks_by_status.items():
            content += f"## {status} Tasks\n\n"
            for task in task_list:
                content += f"### {task['id']}: {task['title']}\n\n"
                content += f"- **Status**: {task['status']}\n"
                content += f"- **Priority**: {task['priority']}\n"
                content += f"- **Effort**: {task['effort'] or 'Not specified'}\n"
                content += f"- **Assignee**: {task['assignee'] or 'Unassigned'}\n"
                content += f"- **Created**: {task['created_at']}\n"
                content += f"- **Updated**: {task['updated_at']}\n\n"

                if task["user_story"]:
                    content += f"**User Story**: {task['user_story']}\n\n"

                if task["acceptance_criteria"]:
                    acc = self._safe_json_loads(task["acceptance_criteria"])
                    if acc:
                        content += "**Acceptance Criteria**:\n"
                        for ac in acc:
                            content += f"- {ac}\n"
                        content += "\n"

                # Linked requirements via relationships table
                linked_reqs = await self.db.execute_query(
                    """
                    SELECT r.id, r.title FROM requirements r
                    JOIN relationships rel
                      ON rel.source_type = 'requirement'
                     AND rel.source_id = r.id
                     AND rel.target_type = 'task'
                     AND rel.target_id = ?
                    WHERE rel.project_id = ?
                    """,
                    [task["id"], project_id],
                    fetch_all=True,
                    row_factory=True,
                )

                if linked_reqs:
                    content += "**Linked Requirements**:\n"
                    for req in linked_reqs:
                        content += f"- {req['id']}: {req['title']}\n"
                    content += "\n"

                content += "---\n\n"

        await asyncio.to_thread(Path(filepath).write_text, content, encoding="utf-8")

        return [filename]

    async def _export_architecture(
        self, project_id: str, project_name: str, output_dir: str
    ) -> list[str]:
        """Export architecture decisions to markdown file, scoped to project."""
        architecture = await self.db.get_records(
            "architecture",
            "*",
            where_clause="project_id = ? AND is_archived = 0",
            where_params=[project_id],
            order_by="created_at DESC",
        )

        if not architecture:
            return []

        filename = f"{project_name}-architecture.md"
        filepath = os.path.join(output_dir, filename)

        content = f"# {project_name} - Architecture Documentation\n\n"
        content += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

        for arch in architecture:
            content += f"## {arch['id']}: {arch['title']}\n\n"
            content += f"- **Status**: {arch['status']}\n"
            content += f"- **Created**: {arch['created_at']}\n"
            content += f"- **Updated**: {arch['updated_at']}\n\n"

            if arch["authors"]:
                authors = self._safe_json_loads(arch["authors"])
                if authors:
                    content += f"- **Authors**: {', '.join(authors)}\n\n"

            if arch["context"]:
                content += f"### Context\n{arch['context']}\n\n"
            if arch["decision"]:
                content += f"### Decision\n{arch['decision']}\n\n"

            if arch["decision_drivers"]:
                drivers = self._safe_json_loads(arch["decision_drivers"])
                if drivers:
                    content += "### Decision Drivers\n"
                    for driver in drivers:
                        content += f"- {driver}\n"
                    content += "\n"

            if arch["considered_options"]:
                options = self._safe_json_loads(arch["considered_options"])
                if options:
                    content += "### Considered Options\n"
                    for option in options:
                        content += f"- {option}\n"
                    content += "\n"

            if arch["consequences"]:
                consequences = self._safe_json_loads(arch["consequences"])
                if consequences:
                    content += "### Consequences\n"
                    if isinstance(consequences, dict):
                        for key, value in consequences.items():
                            content += f"**{key.title()}**: {value}\n"
                    else:
                        content += f"{consequences}\n"
                    content += "\n"

            # Linked requirements via relationships table
            linked_reqs = await self.db.execute_query(
                """
                SELECT r.id, r.title FROM requirements r
                JOIN relationships rel
                  ON rel.source_type = 'requirement'
                 AND rel.source_id = r.id
                 AND rel.target_type = 'architecture'
                 AND rel.target_id = ?
                WHERE rel.project_id = ?
                """,
                [arch["id"], project_id],
                fetch_all=True,
                row_factory=True,
            )

            if linked_reqs:
                content += "### Linked Requirements\n"
                for req in linked_reqs:
                    content += f"- {req['id']}: {req['title']}\n"
                content += "\n"

            content += "---\n\n"

        await asyncio.to_thread(Path(filepath).write_text, content, encoding="utf-8")

        return [filename]

    # ------------------------------------------------------------------
    # create_architectural_diagrams
    # ------------------------------------------------------------------

    async def _create_architectural_diagrams(self, **params) -> list[TextContent]:
        """Generate Mermaid diagrams for project architecture"""
        try:
            project_id = params.get("project_id")
            if not project_id:
                return self._create_error_response("Missing required parameter: project_id")

            # Validate project exists
            project_name = await self._get_project_name(project_id)
            if project_name is None:
                return self._create_error_response(f"Project not found: {project_id}")

            diagram_type = params.get("diagram_type", "full_project")
            include_relationships = params.get("include_relationships", True)
            output_format = params.get("output_format", "mermaid")
            output_path = params.get("output_path")
            if not output_path:
                return self._create_error_response("Missing required parameter: output_path")

            # Validate diagram type
            valid_types = [
                "requirements",
                "tasks",
                "architecture",
                "full_project",
                "directory_structure",
                "dependencies",
            ]
            if diagram_type not in valid_types:
                return self._create_error_response(
                    f"Invalid diagram type: {diagram_type}. Valid types are: {', '.join(valid_types)}"
                )

            mermaid_content = ""
            requirement_ids = params.get("requirement_ids", [])

            if diagram_type == "requirements":
                mermaid_content = await self._generate_requirements_diagram(project_id, requirement_ids)
            elif diagram_type == "tasks":
                mermaid_content = await self._generate_tasks_diagram(project_id, requirement_ids)
            elif diagram_type == "architecture":
                mermaid_content = await self._generate_architecture_diagram(project_id, requirement_ids)
            elif diagram_type == "full_project":
                mermaid_content = await self._generate_full_project_diagram(
                    project_id, include_relationships, requirement_ids
                )
            elif diagram_type == "directory_structure":
                mermaid_content = self._generate_directory_structure_diagram()
            elif diagram_type == "dependencies":
                mermaid_content = await self._generate_dependencies_diagram(project_id, requirement_ids)

            if not mermaid_content:
                return self._create_above_fold_response(
                    "INFO",
                    "No data found for diagram",
                    f"Check if {diagram_type} data exists in the system",
                )

            # Prepare content for output
            if output_format == "markdown_with_mermaid":
                file_content = f"```mermaid\n{mermaid_content}\n```"
            else:
                file_content = mermaid_content

            # Save to file (output_path is required)
            if not self._validate_output_path(output_path):
                return self._create_error_response("Invalid output path specified")

            if not self._ensure_output_directory(output_path):
                return self._create_error_response(f"Cannot create output directory: {output_path}")

            filename = self._generate_diagram_filename(diagram_type, output_format)
            full_path = os.path.join(output_path, filename)

            try:
                await asyncio.to_thread(
                    Path(full_path).write_text, file_content, encoding="utf-8"
                )
            except (OSError, PermissionError) as e:
                return self._create_error_response(f"Failed to save diagram file: {str(e)}")

            key_info = f"{diagram_type.replace('_', ' ').title()} diagram generated"
            action_info = f"{output_format} format | Saved to {full_path}"

            return self._create_above_fold_response("SUCCESS", key_info, action_info)

        except Exception as e:
            return self._create_error_response("Failed to create architectural diagram", e)

    # ------------------------------------------------------------------
    # Diagram generators (all project-scoped)
    # ------------------------------------------------------------------

    async def _generate_requirements_diagram(
        self, project_id: str, requirement_ids: list[str] | None = None
    ) -> str:
        """Generate requirements flowchart, scoped to project."""
        if requirement_ids:
            placeholders = ",".join(["?"] * len(requirement_ids))
            requirements = await self.db.execute_query(
                f"SELECT * FROM requirements WHERE id IN ({placeholders}) "
                f"AND project_id = ? AND is_archived = 0 ORDER BY type, id",
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
        else:
            requirements = await self.db.get_records(
                "requirements",
                "*",
                where_clause="project_id = ? AND is_archived = 0",
                where_params=[project_id],
                order_by="type, id",
            )

        if not requirements:
            return ""

        mermaid_content = "flowchart TD\n"

        # Group by type
        req_by_type: dict[str, list] = {}
        for req in requirements:
            req_type = req["type"]
            if req_type not in req_by_type:
                req_by_type[req_type] = []
            req_by_type[req_type].append(req)

        for req_type in req_by_type:
            mermaid_content += f"    {req_type}[{req_type} Requirements]\n"

        for req_type, reqs in req_by_type.items():
            for req in reqs:
                node_id = req["id"].replace("-", "_")
                status_color = {
                    "Draft": "fill:#ff9999",
                    "Under Review": "fill:#ffcc99",
                    "Approved": "fill:#99ccff",
                    "Ready": "fill:#99ff99",
                    "Implemented": "fill:#ccffcc",
                    "Validated": "fill:#99ff99",
                    "Deprecated": "fill:#cccccc",
                }.get(req["status"], "fill:#ffffff")

                title_short = req["title"][:30] + "..." if len(req["title"]) > 30 else req["title"]
                mermaid_content += f'    {node_id}["{req["id"]}<br/>{title_short}"]\n'
                mermaid_content += f"    {req_type} --> {node_id}\n"
                mermaid_content += f"    style {node_id} {status_color}\n"

        return mermaid_content

    async def _generate_tasks_diagram(
        self, project_id: str, requirement_ids: list[str] | None = None
    ) -> str:
        """Generate task hierarchy diagram, scoped to project."""
        if requirement_ids:
            placeholders = ",".join(["?"] * len(requirement_ids))
            tasks = await self.db.execute_query(
                f"""
                SELECT DISTINCT t.* FROM tasks t
                JOIN relationships rel
                  ON rel.source_type = 'requirement'
                 AND rel.target_type = 'task'
                 AND rel.target_id = t.id
                WHERE rel.source_id IN ({placeholders})
                  AND t.project_id = ? AND t.is_archived = 0
                ORDER BY t.id
                """,
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
        else:
            tasks = await self.db.get_records(
                "tasks",
                "*",
                where_clause="project_id = ? AND is_archived = 0",
                where_params=[project_id],
                order_by="id",
            )

        if not tasks:
            return ""

        mermaid_content = "flowchart TD\n"

        for task in tasks:
            node_id = task["id"].replace("-", "_")
            status_color = {
                "Not Started": "fill:#ff9999",
                "In Progress": "fill:#ffcc99",
                "Blocked": "fill:#ff6666",
                "Complete": "fill:#99ff99",
                "Abandoned": "fill:#cccccc",
            }.get(task["status"], "fill:#ffffff")

            title_short = task["title"][:30] + "..." if len(task["title"]) > 30 else task["title"]
            mermaid_content += f'    {node_id}["{task["id"]}<br/>{title_short}"]\n'
            mermaid_content += f"    style {node_id} {status_color}\n"

            # Parent-child via parent_task_id column
            if task["parent_task_id"]:
                parent_id = task["parent_task_id"].replace("-", "_")
                mermaid_content += f"    {parent_id} --> {node_id}\n"

        return mermaid_content

    async def _generate_architecture_diagram(
        self, project_id: str, requirement_ids: list[str] | None = None
    ) -> str:
        """Generate architecture decisions diagram, scoped to project."""
        if requirement_ids:
            placeholders = ",".join(["?"] * len(requirement_ids))
            architecture = await self.db.execute_query(
                f"""
                SELECT DISTINCT a.* FROM architecture a
                JOIN relationships rel
                  ON rel.source_type = 'requirement'
                 AND rel.target_type = 'architecture'
                 AND rel.target_id = a.id
                WHERE rel.source_id IN ({placeholders})
                  AND a.project_id = ? AND a.is_archived = 0
                ORDER BY a.created_at DESC
                """,
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
        else:
            architecture = await self.db.get_records(
                "architecture",
                "*",
                where_clause="project_id = ? AND is_archived = 0",
                where_params=[project_id],
                order_by="created_at DESC",
            )

        if not architecture:
            return ""

        mermaid_content = "flowchart TD\n"

        for arch in architecture:
            node_id = arch["id"].replace("-", "_")
            status_color = {
                "Proposed": "fill:#ffcc99",
                "Accepted": "fill:#99ff99",
                "Rejected": "fill:#ff9999",
                "Deprecated": "fill:#cccccc",
            }.get(arch["status"], "fill:#ffffff")

            title_short = arch["title"][:30] + "..." if len(arch["title"]) > 30 else arch["title"]
            mermaid_content += f'    {node_id}["{arch["id"]}<br/>{title_short}"]\n'
            mermaid_content += f"    style {node_id} {status_color}\n"

        return mermaid_content

    async def _generate_full_project_diagram(
        self, project_id: str, include_relationships: bool, requirement_ids: list[str] | None = None
    ) -> str:
        """Generate full project overview diagram, scoped to project."""
        if requirement_ids:
            placeholders = ",".join(["?"] * len(requirement_ids))
            requirements = await self.db.execute_query(
                f"SELECT * FROM requirements WHERE id IN ({placeholders}) "
                f"AND project_id = ? AND is_archived = 0 ORDER BY type, id",
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
            tasks = await self.db.execute_query(
                f"""
                SELECT DISTINCT t.* FROM tasks t
                JOIN relationships rel
                  ON rel.source_type = 'requirement'
                 AND rel.target_type = 'task'
                 AND rel.target_id = t.id
                WHERE rel.source_id IN ({placeholders})
                  AND t.project_id = ? AND t.is_archived = 0
                ORDER BY t.id
                """,
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
            architecture = await self.db.execute_query(
                f"""
                SELECT DISTINCT a.* FROM architecture a
                JOIN relationships rel
                  ON rel.source_type = 'requirement'
                 AND rel.target_type = 'architecture'
                 AND rel.target_id = a.id
                WHERE rel.source_id IN ({placeholders})
                  AND a.project_id = ? AND a.is_archived = 0
                ORDER BY a.created_at DESC
                """,
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
        else:
            requirements = await self.db.get_records(
                "requirements", "*",
                where_clause="project_id = ? AND is_archived = 0",
                where_params=[project_id],
                order_by="type, id",
            )
            tasks = await self.db.get_records(
                "tasks", "*",
                where_clause="project_id = ? AND is_archived = 0",
                where_params=[project_id],
                order_by="id",
            )
            architecture = await self.db.get_records(
                "architecture", "*",
                where_clause="project_id = ? AND is_archived = 0",
                where_params=[project_id],
                order_by="created_at DESC",
            )

        mermaid_content = "flowchart TD\n"
        mermaid_content += "    Requirements[Requirements]\n"
        mermaid_content += "    Tasks[Tasks]\n"
        mermaid_content += "    Architecture[Architecture]\n"

        # Add requirements (limit to first 10)
        for req in requirements[:10]:
            node_id = req["id"].replace("-", "_")
            title_short = req["title"][:20] + "..." if len(req["title"]) > 20 else req["title"]
            mermaid_content += f'    {node_id}["{req["id"]}<br/>{title_short}"]\n'
            mermaid_content += f"    Requirements --> {node_id}\n"

        # Add tasks (limit to first 10)
        for task in tasks[:10]:
            node_id = task["id"].replace("-", "_")
            title_short = task["title"][:20] + "..." if len(task["title"]) > 20 else task["title"]
            mermaid_content += f'    {node_id}["{task["id"]}<br/>{title_short}"]\n'
            mermaid_content += f"    Tasks --> {node_id}\n"

        # Add architecture (limit to first 5)
        for arch in architecture[:5]:
            node_id = arch["id"].replace("-", "_")
            title_short = arch["title"][:20] + "..." if len(arch["title"]) > 20 else arch["title"]
            mermaid_content += f'    {node_id}["{arch["id"]}<br/>{title_short}"]\n'
            mermaid_content += f"    Architecture --> {node_id}\n"

        # Add relationships if requested
        if include_relationships:
            rels = await self.db.execute_query(
                """
                SELECT source_id, target_id, relationship_type
                FROM relationships
                WHERE project_id = ?
                  AND source_type IN ('requirement', 'task', 'architecture')
                  AND target_type IN ('requirement', 'task', 'architecture')
                LIMIT 20
                """,
                [project_id],
                fetch_all=True,
                row_factory=True,
            )
            for rel in rels:
                src = rel["source_id"].replace("-", "_")
                tgt = rel["target_id"].replace("-", "_")
                mermaid_content += f"    {src} -.-> {tgt}\n"

        return mermaid_content

    def _generate_directory_structure_diagram(self) -> str:
        """Generate directory structure diagram"""
        return """flowchart TD
    Root[Project Root]
    Src[src/]
    Docs[docs/]
    Tests[tests/]
    Root --> Src
    Root --> Docs
    Root --> Tests"""

    async def _generate_dependencies_diagram(
        self, project_id: str, requirement_ids: list[str] | None = None
    ) -> str:
        """Generate dependencies diagram using relationships table, scoped to project."""
        if requirement_ids:
            # Get task IDs linked to these requirements
            placeholders = ",".join(["?"] * len(requirement_ids))
            task_id_rows = await self.db.execute_query(
                f"""
                SELECT DISTINCT rel.target_id AS task_id
                FROM relationships rel
                WHERE rel.source_type = 'requirement'
                  AND rel.target_type = 'task'
                  AND rel.source_id IN ({placeholders})
                  AND rel.project_id = ?
                """,
                requirement_ids + [project_id],
                fetch_all=True,
                row_factory=True,
            )
            task_ids = [row["task_id"] for row in task_id_rows]

            if task_ids:
                task_placeholders = ",".join(["?"] * len(task_ids))
                dependencies = await self.db.execute_query(
                    f"""
                    SELECT rel.source_id AS task_id, rel.target_id AS depends_on_task_id
                    FROM relationships rel
                    WHERE rel.source_type = 'task'
                      AND rel.target_type = 'task'
                      AND rel.relationship_type IN ('depends', 'blocks')
                      AND rel.project_id = ?
                      AND (rel.source_id IN ({task_placeholders})
                       OR rel.target_id IN ({task_placeholders}))
                    """,
                    [project_id] + task_ids + task_ids,
                    fetch_all=True,
                    row_factory=True,
                )
            else:
                dependencies = []
        else:
            dependencies = await self.db.execute_query(
                """
                SELECT rel.source_id AS task_id, rel.target_id AS depends_on_task_id
                FROM relationships rel
                JOIN tasks t1 ON rel.source_id = t1.id AND t1.is_archived = 0
                JOIN tasks t2 ON rel.target_id = t2.id AND t2.is_archived = 0
                WHERE rel.source_type = 'task'
                  AND rel.target_type = 'task'
                  AND rel.relationship_type IN ('depends', 'blocks')
                  AND rel.project_id = ?
                """,
                [project_id],
                fetch_all=True,
                row_factory=True,
            )

        if not dependencies:
            return "flowchart TD\n    NoDeps[No task dependencies found]\n"

        mermaid_content = "flowchart TD\n"

        for dep in dependencies:
            task_id = dep["task_id"].replace("-", "_")
            depends_on = dep["depends_on_task_id"].replace("-", "_")
            mermaid_content += f"    {depends_on} --> {task_id}\n"

        return mermaid_content

    # ------------------------------------------------------------------
    # File utilities
    # ------------------------------------------------------------------

    def _get_diagram_file_extension(self, output_format: str) -> str:
        """Get appropriate file extension based on output format"""
        if output_format == "markdown_with_mermaid":
            return ".md"
        return ".mmd"

    def _generate_diagram_filename(self, diagram_type: str, output_format: str) -> str:
        """Generate structured filename for diagram files"""
        safe_diagram_type = diagram_type.replace("_", "-").lower()
        timestamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        extension = self._get_diagram_file_extension(output_format)
        return f"{safe_diagram_type}-diagram-{timestamp}{extension}"

    def _validate_output_path(self, output_path: str) -> bool:
        """Validate output path for security (prevent path traversal)"""
        if not output_path:
            return False
        return ".." not in output_path

    def _ensure_output_directory(self, output_path: str) -> bool:
        """Create output directory if it doesn't exist"""
        try:
            os.makedirs(output_path, exist_ok=True)
            return True
        except (OSError, PermissionError):
            return False
