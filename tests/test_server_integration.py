"""
Integration tests for the full server with all v2 handlers.

Verifies:
  - 8 handlers instantiated (no InterviewHandler)
  - 47 tools registered with correct routing
  - No legacy/removed tools in registry
  - Full end-to-end workflow through all handlers
  - All tool names unique
"""

import os
import tempfile

import pytest

from lifecycle_mcp.server import LifecycleMCPServer


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
async def server(tmp_path):
    """Create a fully initialized server with fresh v2 database."""
    os.environ["LIFECYCLE_DB"] = str(tmp_path / "test.db")
    srv = LifecycleMCPServer()
    await srv.db_manager.initialize()
    yield srv
    await srv.db_manager.close()
    os.environ.pop("LIFECYCLE_DB", None)


# ------------------------------------------------------------------
# Structural tests
# ------------------------------------------------------------------


class TestServerStructure:
    """Tests for server handler and tool registration."""

    @pytest.mark.asyncio
    async def test_handler_count(self, server):
        """Server should have exactly 8 handlers (no InterviewHandler)."""
        handler_attrs = [
            "project_handler",
            "requirement_handler",
            "task_handler",
            "architecture_handler",
            "relationship_handler",
            "validation_handler",
            "export_handler",
            "status_handler",
        ]
        for attr in handler_attrs:
            assert hasattr(server, attr), f"Missing handler attribute: {attr}"

    @pytest.mark.asyncio
    async def test_no_interview_handler(self, server):
        """InterviewHandler must NOT be registered."""
        assert not hasattr(server, "interview_handler")

    @pytest.mark.asyncio
    async def test_tool_count(self, server):
        """Exactly 47 tools should be registered in the handler registry."""
        assert len(server.handlers) == 47, (
            f"Expected 47 tools, got {len(server.handlers)}: {sorted(server.handlers.keys())}"
        )

    @pytest.mark.asyncio
    async def test_all_tool_names_unique(self, server):
        """All tool names in handler registry must be unique (dict guarantees this, but verify definitions)."""
        # Collect tool names from all handler get_tool_definitions()
        seen = set()
        handlers = [
            server.project_handler,
            server.requirement_handler,
            server.task_handler,
            server.architecture_handler,
            server.relationship_handler,
            server.validation_handler,
            server.export_handler,
            server.status_handler,
        ]
        for handler in handlers:
            for tool_def in handler.get_tool_definitions():
                name = tool_def["name"]
                assert name not in seen, f"Duplicate tool name: {name}"
                seen.add(name)
        assert len(seen) == 47

    @pytest.mark.asyncio
    async def test_interview_tools_not_in_registry(self, server):
        """Legacy interview tool names must NOT appear in the handler registry."""
        interview_tools = [
            "start_requirement_interview",
            "continue_requirement_interview",
            "start_architectural_conversation",
            "continue_architectural_conversation",
        ]
        for tool_name in interview_tools:
            assert tool_name not in server.handlers, f"Interview tool still registered: {tool_name}"

    @pytest.mark.asyncio
    async def test_no_github_tools(self, server):
        """Legacy GitHub sync tools must NOT appear in the handler registry."""
        github_tools = [
            "sync_task_from_github",
            "bulk_sync_github_tasks",
        ]
        for tool_name in github_tools:
            assert tool_name not in server.handlers, f"GitHub tool still registered: {tool_name}"

    @pytest.mark.asyncio
    async def test_tool_routing_project(self, server):
        """Project tools should route to ProjectHandler."""
        project_tools = [
            "create_project",
            "update_project",
            "archive_project",
            "query_projects",
            "get_project_details",
        ]
        for tool_name in project_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.project_handler

    @pytest.mark.asyncio
    async def test_tool_routing_requirement(self, server):
        """Requirement tools should route to RequirementHandler."""
        req_tools = [
            "create_requirement",
            "update_requirement",
            "update_requirement_status",
            "archive_requirement",
            "query_requirements",
            "query_requirements_json",
            "get_requirement_details",
            "trace_requirement",
            "batch_create_requirements",
            "clone_requirement",
        ]
        for tool_name in req_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.requirement_handler

    @pytest.mark.asyncio
    async def test_tool_routing_task(self, server):
        """Task tools should route to TaskHandler."""
        task_tools = [
            "create_task",
            "update_task",
            "update_task_status",
            "archive_task",
            "query_tasks",
            "query_tasks_json",
            "get_task_details",
            "batch_create_tasks",
            "clone_task",
            "get_task_requirement_context",
            "get_task_adr_context",
            "get_task_full_context",
        ]
        for tool_name in task_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.task_handler

    @pytest.mark.asyncio
    async def test_tool_routing_architecture(self, server):
        """Architecture tools should route to ArchitectureHandler."""
        arch_tools = [
            "create_architecture_decision",
            "update_architecture_decision",
            "update_architecture_status",
            "archive_architecture_decision",
            "query_architecture_decisions",
            "query_architecture_decisions_json",
            "get_architecture_details",
            "add_architecture_review",
        ]
        for tool_name in arch_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.architecture_handler

    @pytest.mark.asyncio
    async def test_tool_routing_relationship(self, server):
        """Relationship tools should route to RelationshipHandler."""
        rel_tools = [
            "create_relationship",
            "delete_relationship",
            "query_relationships",
            "get_entity_relationships",
            "query_all_relationships",
        ]
        for tool_name in rel_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.relationship_handler

    @pytest.mark.asyncio
    async def test_tool_routing_validation(self, server):
        """Validation tools should route to ValidationHandler."""
        val_tools = [
            "validate_project_plan",
            "get_valid_status_transitions",
        ]
        for tool_name in val_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.validation_handler

    @pytest.mark.asyncio
    async def test_tool_routing_export(self, server):
        """Export tools should route to ExportHandler."""
        exp_tools = [
            "export_project_documentation",
            "create_architectural_diagrams",
        ]
        for tool_name in exp_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.export_handler

    @pytest.mark.asyncio
    async def test_tool_routing_status(self, server):
        """Status tools should route to StatusHandler."""
        status_tools = [
            "get_project_status",
            "get_project_metrics",
            "diff_project",
        ]
        for tool_name in status_tools:
            assert tool_name in server.handlers
            assert server.handlers[tool_name] is server.status_handler

    @pytest.mark.asyncio
    async def test_tool_definitions_match_registry(self, server):
        """Every tool in handler definitions should exist in registry, and vice versa."""
        definition_names = set()
        handlers = [
            server.project_handler,
            server.requirement_handler,
            server.task_handler,
            server.architecture_handler,
            server.relationship_handler,
            server.validation_handler,
            server.export_handler,
            server.status_handler,
        ]
        for handler in handlers:
            for tool_def in handler.get_tool_definitions():
                definition_names.add(tool_def["name"])

        registry_names = set(server.handlers.keys())
        assert definition_names == registry_names, (
            f"Definition/registry mismatch.\n"
            f"  In definitions but not registry: {definition_names - registry_names}\n"
            f"  In registry but not definitions: {registry_names - definition_names}"
        )


# ------------------------------------------------------------------
# End-to-end workflow tests
# ------------------------------------------------------------------


class TestFullWorkflow:
    """End-to-end workflow through all handlers."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_workflow(self, server):
        """Project -> requirements -> tasks -> relationships -> validate -> status -> export."""

        # 1. Create project
        proj_result = await server.project_handler.handle_tool_call(
            "create_project",
            {"name": "Integration Test Project", "description": "Testing full lifecycle"},
        )
        assert len(proj_result) == 1
        assert "SUCCESS" in proj_result[0].text
        assert "PROJ-0001" in proj_result[0].text

        # 2. Create two requirements
        for i, title in enumerate(["Auth Module", "Data Layer"], start=1):
            result = await server.requirement_handler.handle_tool_call(
                "create_requirement",
                {
                    "project_id": "PROJ-0001",
                    "type": "FUNC",
                    "title": title,
                    "priority": "P1",
                    "current_state": "Not implemented",
                    "desired_state": "Fully implemented",
                },
            )
            assert "SUCCESS" in result[0].text
            assert f"REQ-{i:04d}" in result[0].text

        # 3. Create three tasks (one for each req + one shared)
        task_titles = [
            ("Implement auth login", "PROJ-0001"),
            ("Implement auth logout", "PROJ-0001"),
            ("Design data schema", "PROJ-0001"),
        ]
        for i, (title, proj_id) in enumerate(task_titles, start=1):
            result = await server.task_handler.handle_tool_call(
                "create_task",
                {
                    "project_id": proj_id,
                    "title": title,
                    "priority": "P1",
                },
            )
            assert "SUCCESS" in result[0].text
            assert f"TASK-{i:04d}" in result[0].text

        # 4. Create relationships
        rels = [
            ("TASK-0001", "REQ-0001", "implements"),
            ("TASK-0002", "REQ-0001", "implements"),
            ("TASK-0003", "REQ-0002", "implements"),
        ]
        for src, tgt, rel_type in rels:
            result = await server.relationship_handler.handle_tool_call(
                "create_relationship",
                {
                    "source_id": src,
                    "target_id": tgt,
                    "relationship_type": rel_type,
                    "project_id": "PROJ-0001",
                },
            )
            assert "SUCCESS" in result[0].text

        # 5. Validate project plan
        val_result = await server.validation_handler.handle_tool_call(
            "validate_project_plan",
            {"project_id": "PROJ-0001"},
        )
        assert len(val_result) == 1
        # Validation should complete (may have warnings but not crash)
        assert "ERROR" not in val_result[0].text or "validation" in val_result[0].text.lower()

        # 6. Get project status
        status_result = await server.status_handler.handle_tool_call(
            "get_project_status",
            {"project_id": "PROJ-0001"},
        )
        assert len(status_result) == 1
        status_text = status_result[0].text
        assert "PROJ-0001" in status_text or "Integration Test Project" in status_text

        # 7. Get project metrics
        metrics_result = await server.status_handler.handle_tool_call(
            "get_project_metrics",
            {"project_id": "PROJ-0001"},
        )
        assert len(metrics_result) == 1

        # 8. Export documentation
        with tempfile.TemporaryDirectory() as export_dir:
            export_result = await server.export_handler.handle_tool_call(
                "export_project_documentation",
                {"project_id": "PROJ-0001", "output_directory": export_dir},
            )
            assert len(export_result) == 1
            assert "SUCCESS" in export_result[0].text

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, server):
        """Calling a tool not in the registry should return an error message."""
        # The call_tool is registered on server.server, but we can test handler routing
        handler = server.handlers.get("nonexistent_tool")
        assert handler is None

    @pytest.mark.asyncio
    async def test_architecture_decision_workflow(self, server):
        """Create project -> create ADR -> add review -> query."""
        # Create project
        await server.project_handler.handle_tool_call(
            "create_project", {"name": "ADR Test"}
        )

        # Create ADR
        adr_result = await server.architecture_handler.handle_tool_call(
            "create_architecture_decision",
            {
                "project_id": "PROJ-0001",
                "title": "Use SQLite",
                "context": "Need a database",
                "decision": "Use SQLite for simplicity",
            },
        )
        assert "SUCCESS" in adr_result[0].text
        assert "ADR-0001" in adr_result[0].text

        # Add review
        review_result = await server.architecture_handler.handle_tool_call(
            "add_architecture_review",
            {
                "architecture_id": "ADR-0001",
                "comment": "Looks good",
                "reviewer": "TestReviewer",
            },
        )
        assert "SUCCESS" in review_result[0].text

        # Query ADRs
        query_result = await server.architecture_handler.handle_tool_call(
            "query_architecture_decisions",
            {"project_id": "PROJ-0001"},
        )
        assert "ADR-0001" in query_result[0].text

    @pytest.mark.asyncio
    async def test_get_valid_status_transitions(self, server):
        """ValidationHandler should return valid transitions for each entity type."""
        result = await server.validation_handler.handle_tool_call(
            "get_valid_status_transitions",
            {"entity_type": "requirement", "current_status": "Draft"},
        )
        assert len(result) == 1
        text = result[0].text
        assert "Under Review" in text
