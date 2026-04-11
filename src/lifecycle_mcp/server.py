#!/usr/bin/env python3
"""
MCP Server for Software Lifecycle Management (v2)

Provides structured access to projects, requirements, tasks, and
architecture decisions through 8 handler modules and 47 tools.
"""

import argparse
import asyncio
import logging
import os
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from .database_manager import DatabaseManager
from .handlers import (
    ArchitectureHandler,
    ExportHandler,
    ProjectHandler,
    RelationshipHandler,
    RequirementHandler,
    StatusHandler,
    TaskHandler,
    ValidationHandler,
)

logger = logging.getLogger(__name__)


class LifecycleMCPServer:
    """MCP Server using modular handler architecture (v2)"""

    def __init__(self):
        """Initialize server with database manager and 8 handlers"""
        # Initialize database manager
        self.db_manager = DatabaseManager()

        # Initialize handlers (8 total)
        self.project_handler = ProjectHandler(self.db_manager)
        self.requirement_handler = RequirementHandler(self.db_manager)
        self.task_handler = TaskHandler(self.db_manager)
        self.architecture_handler = ArchitectureHandler(self.db_manager)
        self.relationship_handler = RelationshipHandler(self.db_manager)
        self.validation_handler = ValidationHandler(self.db_manager)
        self.export_handler = ExportHandler(self.db_manager)
        self.status_handler = StatusHandler(self.db_manager)

        # Create handler registry for tool routing (47 tools)
        self.handlers = {
            # Project tools (5)
            "create_project": self.project_handler,
            "update_project": self.project_handler,
            "archive_project": self.project_handler,
            "query_projects": self.project_handler,
            "get_project_details": self.project_handler,
            # Requirement tools (10)
            "create_requirement": self.requirement_handler,
            "update_requirement": self.requirement_handler,
            "update_requirement_status": self.requirement_handler,
            "archive_requirement": self.requirement_handler,
            "query_requirements": self.requirement_handler,
            "query_requirements_json": self.requirement_handler,
            "get_requirement_details": self.requirement_handler,
            "trace_requirement": self.requirement_handler,
            "batch_create_requirements": self.requirement_handler,
            "clone_requirement": self.requirement_handler,
            # Task tools (12)
            "create_task": self.task_handler,
            "update_task": self.task_handler,
            "update_task_status": self.task_handler,
            "archive_task": self.task_handler,
            "query_tasks": self.task_handler,
            "query_tasks_json": self.task_handler,
            "get_task_details": self.task_handler,
            "batch_create_tasks": self.task_handler,
            "clone_task": self.task_handler,
            "get_task_requirement_context": self.task_handler,
            "get_task_adr_context": self.task_handler,
            "get_task_full_context": self.task_handler,
            # Architecture tools (8)
            "create_architecture_decision": self.architecture_handler,
            "update_architecture_decision": self.architecture_handler,
            "update_architecture_status": self.architecture_handler,
            "archive_architecture_decision": self.architecture_handler,
            "query_architecture_decisions": self.architecture_handler,
            "query_architecture_decisions_json": self.architecture_handler,
            "get_architecture_details": self.architecture_handler,
            "add_architecture_review": self.architecture_handler,
            # Relationship tools (5)
            "create_relationship": self.relationship_handler,
            "delete_relationship": self.relationship_handler,
            "query_relationships": self.relationship_handler,
            "get_entity_relationships": self.relationship_handler,
            "query_all_relationships": self.relationship_handler,
            # Validation tools (2)
            "validate_project_plan": self.validation_handler,
            "get_valid_status_transitions": self.validation_handler,
            # Export tools (2)
            "export_project_documentation": self.export_handler,
            "create_architectural_diagrams": self.export_handler,
            # Status tools (3)
            "get_project_status": self.status_handler,
            "get_project_metrics": self.status_handler,
            "diff_project": self.status_handler,
        }

        # Create MCP server instance
        self.server = Server("lifecycle-management")
        self._register_handlers()

    def _register_handlers(self):
        """Register MCP server handlers"""

        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            """List available tools from all handlers"""
            tools = []

            # Collect tool definitions from all handlers
            for handler in [
                self.project_handler,
                self.requirement_handler,
                self.task_handler,
                self.architecture_handler,
                self.relationship_handler,
                self.validation_handler,
                self.export_handler,
                self.status_handler,
            ]:
                handler_tools = handler.get_tool_definitions()
                # Convert to Tool objects
                for tool_def in handler_tools:
                    tools.append(
                        Tool(
                            name=tool_def["name"],
                            description=tool_def["description"],
                            inputSchema=tool_def["inputSchema"],
                        )
                    )

            logger.info(f"Registered {len(tools)} MCP tools")
            return tools

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
            """Route tool calls to appropriate handlers

            Note: This method is async and must await handler calls for proper MCP protocol compliance.
            All handler.handle_tool_call() methods must also be async to prevent connection issues.
            """
            try:
                # Find the appropriate handler for this tool
                handler = self.handlers.get(name)
                if not handler:
                    logger.error(f"No handler found for tool: {name}")
                    return [TextContent(type="text", text=f"Unknown tool: {name}")]

                # Delegate to the handler
                logger.debug(f"Routing tool '{name}' to {handler.__class__.__name__}")
                return await handler.handle_tool_call(name, arguments)

            except Exception as e:
                logger.error(f"Error handling tool '{name}': {str(e)}")
                return [TextContent(type="text", text=f"Error handling {name}: {str(e)}")]

    async def run_stdio(self):
        """Run the MCP server using stdio transport"""
        logger.info("Starting Lifecycle MCP Server (stdio transport)")
        async with stdio_server() as (read_stream, write_stream):
            await self.server.run(read_stream, write_stream, self.server.create_initialization_options())

    async def run_streamable_http(self, host: str, port: int) -> None:
        """Run the MCP server using streamable HTTP transport.

        Creates a Starlette ASGI application backed by the MCP SDK's
        ``StreamableHTTPSessionManager`` and serves it with uvicorn.

        When *host* is ``"0.0.0.0"`` DNS-rebinding protection is disabled so
        that LAN clients can connect without ``Host`` header issues.

        Args:
            host: Bind address for the HTTP server (e.g. ``"127.0.0.1"`` or ``"0.0.0.0"``).
            port: TCP port number to listen on.
        """
        # Lazy imports to keep stdio startup fast
        import contextlib
        from collections.abc import AsyncIterator

        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.applications import Starlette
        from starlette.routing import Mount

        logger.info("Starting Lifecycle MCP Server (streamable-http transport on %s:%s)", host, port)

        # When binding to all interfaces, disable DNS-rebinding protection so
        # that LAN clients whose Host header doesn't match localhost can connect.
        if host == "0.0.0.0":
            security_settings: TransportSecuritySettings | None = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )
        else:
            security_settings = None  # SDK default (protection disabled for backwards compat)

        session_manager = StreamableHTTPSessionManager(
            app=self.server,
            security_settings=security_settings,
        )

        @contextlib.asynccontextmanager
        async def lifespan(app: Starlette) -> AsyncIterator[None]:
            async with session_manager.run():
                yield

        starlette_app = Starlette(
            routes=[Mount("/mcp", app=session_manager.handle_request)],
            lifespan=lifespan,
        )

        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level="info",
        )
        uvicorn_server = uvicorn.Server(config)
        await uvicorn_server.serve()

    async def run_sse(self, host: str, port: int) -> None:
        """Run the MCP server using SSE transport.

        Creates a Starlette application with:
        - GET /sse : SSE stream endpoint (returns an ``endpoint`` event)
        - POST /messages/ : client-to-server message endpoint

        When *host* is ``"0.0.0.0"`` DNS-rebinding protection is disabled so
        that LAN clients can connect without ``Host`` header issues.

        Args:
            host: Bind address for the HTTP server.
            port: Port number for the HTTP server.
        """
        # Lazy imports to keep stdio startup fast
        import uvicorn
        from mcp.server.sse import SseServerTransport
        from mcp.server.transport_security import TransportSecuritySettings
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Mount, Route

        logger.info(f"Starting Lifecycle MCP Server (SSE transport on {host}:{port})")

        # When binding to all interfaces, disable DNS-rebinding protection so
        # that LAN clients whose Host header doesn't match localhost can connect.
        if host == "0.0.0.0":
            security_settings = TransportSecuritySettings(
                enable_dns_rebinding_protection=False,
            )
        else:
            security_settings = TransportSecuritySettings(
                enable_dns_rebinding_protection=True,
                allowed_hosts=[
                    host,
                    f"{host}:{port}",
                    f"{host}:*",
                    "localhost",
                    f"localhost:{port}",
                    "localhost:*",
                    "127.0.0.1",
                    f"127.0.0.1:{port}",
                    "127.0.0.1:*",
                ],
                allowed_origins=[
                    f"http://{host}",
                    f"http://{host}:{port}",
                    f"http://{host}:*",
                    "http://localhost",
                    f"http://localhost:{port}",
                    "http://localhost:*",
                    "http://127.0.0.1",
                    f"http://127.0.0.1:{port}",
                    "http://127.0.0.1:*",
                ],
            )

        sse_transport = SseServerTransport(
            "/messages/", security_settings=security_settings
        )

        async def handle_sse(request):
            """Handle incoming SSE connection requests."""
            async with sse_transport.connect_sse(
                request.scope, request.receive, request._send
            ) as (read_stream, write_stream):
                await self.server.run(
                    read_stream,
                    write_stream,
                    self.server.create_initialization_options(),
                )
            # Return empty response to prevent NoneType error on disconnect
            return Response()

        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse, methods=["GET"]),
                Mount("/messages/", app=sse_transport.handle_post_message),
            ],
        )

        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_level="info",
        )
        server = uvicorn.Server(config)
        await server.serve()


# Global server instance for backwards compatibility
_server_instance = None


def get_server_instance() -> LifecycleMCPServer:
    """Get or create the global server instance"""
    global _server_instance
    if _server_instance is None:
        _server_instance = LifecycleMCPServer()
    return _server_instance


async def amain(
    transport: str = "stdio", host: str = "127.0.0.1", port: int = 8080
):
    """Run the MCP server with the specified transport.

    Args:
        transport: Transport type - "stdio", "sse", or "streamable-http".
        host: Host address for network transports.
        port: Port number for network transports.
    """
    server_instance = get_server_instance()
    await server_instance.db_manager.initialize()
    try:
        if transport == "stdio":
            await server_instance.run_stdio()
        elif transport == "sse":
            await server_instance.run_sse(host, port)
        elif transport == "streamable-http":
            await server_instance.run_streamable_http(host, port)
        else:
            raise ValueError(f"Unknown transport: {transport}")
    finally:
        await server_instance.db_manager.close()


def main():
    """Entry point for the lifecycle-mcp command.

    Parses CLI arguments with environment variable fallbacks:
      --transport : LIFECYCLE_TRANSPORT (default: "stdio")
      --host      : LIFECYCLE_HOST     (default: "127.0.0.1")
      --port      : LIFECYCLE_PORT     (default: 8080)

    CLI arguments take precedence over environment variables.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="Lifecycle MCP Server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default=os.environ.get("LIFECYCLE_TRANSPORT", "stdio"),
        help="Transport type (default: stdio, env: LIFECYCLE_TRANSPORT)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("LIFECYCLE_HOST", "127.0.0.1"),
        help="Host address for network transports (default: 127.0.0.1, env: LIFECYCLE_HOST)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LIFECYCLE_PORT", "8080")),
        help="Port for network transports (default: 8080, env: LIFECYCLE_PORT)",
    )
    args = parser.parse_args()

    try:
        asyncio.run(amain(transport=args.transport, host=args.host, port=args.port))
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    except Exception as e:
        logger.error(f"Server error: {str(e)}")
        raise


if __name__ == "__main__":
    main()
