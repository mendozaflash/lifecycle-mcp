# Architecture

## Overview

lifecycle-mcp is a Model Context Protocol (MCP) server for software lifecycle management. It provides structured tracking of projects, requirements, tasks, and architecture decisions through a SQLite database with an async connection pool.

The server exposes **36 tools** across **8 handler modules**, accessible via stdio, streamable-http, or SSE transports.

## Module Inventory

| Module | Path | Purpose |
|--------|------|---------|
| Server | `src/lifecycle_mcp/server.py` | MCP server, handler registry, transport layer |
| Constants | `src/lifecycle_mcp/constants.py` | Shared state machines, relationship rules, entity-table map |
| DatabaseManager | `src/lifecycle_mcp/database_manager.py` | Async SQLite pool, FK enforcement, ID generation |
| Schema | `src/lifecycle_mcp/lifecycle-schema-v2.sql` | v2 database schema (tables, views, triggers, indexes) |
| BaseHandler | `src/lifecycle_mcp/handlers/base_handler.py` | Abstract base class, validation helpers, response formatting |
| ProjectHandler | `src/lifecycle_mcp/handlers/project_handler.py` | Project CRUD and archiving (5 tools) |
| RequirementHandler | `src/lifecycle_mcp/handlers/requirement_handler.py` | Requirement lifecycle management (8 tools) |
| TaskHandler | `src/lifecycle_mcp/handlers/task_handler.py` | Task management with planning/execution fields (8 tools) |
| ArchitectureHandler | `src/lifecycle_mcp/handlers/architecture_handler.py` | ADR management and reviews (7 tools) |
| RelationshipHandler | `src/lifecycle_mcp/handlers/relationship_handler.py` | Polymorphic entity relationships (3 tools) |
| ValidationHandler | `src/lifecycle_mcp/handlers/validation_handler.py` | Plan validation and status transition lookups (2 tools) |
| ExportHandler | `src/lifecycle_mcp/handlers/export_handler.py` | Documentation and diagram export (2 tools) |
| StatusHandler | `src/lifecycle_mcp/handlers/status_handler.py` | Project health diffs (1 tool) |

## Database Schema

### Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `sequences` | Global ID generation | `entity_type`, `next_val` |
| `projects` | First-class project entity | `id` (PROJ-XXXX), `name`, `status`, `tech_stack` (JSON), `constraints` (JSON) |
| `requirements` | Project-scoped requirements | `id` (REQ-XXXX), `project_id` FK, `type`, `status`, `priority` |
| `tasks` | Project-scoped tasks with planning+execution fields | `id` (TASK-XXXX), `project_id` FK, `parent_task_id` FK (self-ref) |
| `architecture` | Architecture Decision Records | `id` (ADR-XXXX), `project_id` FK, `superseded_by` FK (self-ref) |
| `relationships` | Polymorphic entity relationships | `source_type`, `source_id`, `target_type`, `target_id`, `relationship_type` |
| `reviews` | Entity-scoped review comments | `entity_type`, `entity_id`, `reviewer`, `comment` |
| `lifecycle_events` | Status change audit log | `entity_type`, `entity_id`, `event_type`, `from_value`, `to_value` |

### Views

| View | Purpose |
|------|---------|
| `project_summary` | Aggregated counts per project (requirements, tasks, ADRs, completion) |
| `task_hierarchy` | Recursive CTE for parent-child task trees |
| `blocked_tasks` | Tasks in Blocked status with their blocker IDs |

### Triggers

- **Timestamp triggers**: Auto-update `updated_at` on projects, requirements, tasks, architecture
- **Status change logging**: Auto-insert into `lifecycle_events` when requirement or task status changes
- **Task completion**: Auto-set `completed_at` when task status becomes Complete

### Indexes

Indexes on: `relationships(source)`, `relationships(target)`, `relationships(project)`, `relationships(type)`, `requirements(status)`, `requirements(priority)`, `requirements(project)`, `tasks(status)`, `tasks(project)`, `architecture(project)`.

## ID Generation

Global sequential IDs are generated atomically via the `sequences` table. Each entity type has its own counter:

| Entity Type | Prefix | Example |
|-------------|--------|---------|
| project | PROJ | PROJ-0001 |
| requirement | REQ | REQ-0042 |
| task | TASK | TASK-0007 |
| architecture | ADR | ADR-0003 |

The `DatabaseManager.generate_id()` method uses `BEGIN IMMEDIATE` transactions to atomically increment and return the next ID. IDs are globally unique per type -- no project disambiguation needed.

## Relationship Model

A single polymorphic `relationships` table replaces the four legacy join tables (`requirement_tasks`, `requirement_architecture`, `requirement_dependencies`, `task_dependencies`).

Each relationship has:
- `source_type` + `source_id` (e.g., "task", "TASK-0001")
- `target_type` + `target_id` (e.g., "requirement", "REQ-0001")
- `relationship_type`: one of `implements`, `addresses`, `depends`, `blocks`, `informs`, `requires`, `parent`, `refines`, `conflicts`, `relates`
- `project_id`: optional FK scoping the relationship to a project

FK validation happens at the application layer (in `RelationshipHandler`) since SQLite cannot enforce polymorphic FKs.

Valid relationship combinations include:
- `requirement ↔ task` via `implements`
- `task → requirement` via `addresses`
- `requirement ↔ architecture` via `addresses`
- `task → architecture` via `implements` or `informs`
- `architecture → task` via `informs`
- `task ↔ task` via `depends`, `blocks`, `informs`, `requires`
- `requirement ↔ requirement` via `depends`, `parent`, `refines`, `conflicts`, `relates`

The canonical list lives in `src/lifecycle_mcp/constants.py` (`VALID_RELATIONSHIP_COMBINATIONS`).

## Handler Architecture

### Constants Module

`src/lifecycle_mcp/constants.py` is the single source of truth for:
- `REQUIREMENT_TRANSITIONS`, `TASK_TRANSITIONS`, `ARCHITECTURE_TRANSITIONS` — valid state transitions per entity type
- `REQUIREMENT_STATUSES`, `TASK_STATUSES`, `ARCHITECTURE_STATUSES` — derived status sets
- `STATE_MACHINES` — aggregate dict keyed by entity type (used by `ValidationHandler`)
- `VALID_RELATIONSHIP_COMBINATIONS` — set of `(source_type, target_type, relationship_type)` tuples
- `ENTITY_TABLE_MAP` — maps entity type strings to table names

All handlers import from `constants.py`. No state machine definitions exist in handler files.

### BaseHandler

All 8 handlers inherit from `BaseHandler`, which provides:
- Database manager reference (`self.db`)
- Standardized response formatting (`_create_response`, `_create_above_fold_response`, `_create_error_response`)
- Parameter validation (`_validate_required_params`)
- Entity existence checks (`_validate_entity_exists`, `_validate_not_archived`, `_validate_project_exists`)
- Event logging (`_log_operation`)
- JSON serialization helpers (`_safe_json_loads`, `_safe_json_dumps`)
- Abstract methods: `get_tool_definitions()`, `handle_tool_call()`

### Tool Registry

The server maintains a flat `dict[str, BaseHandler]` mapping each of the 36 tool names to its handler instance. Tool routing is O(1) dictionary lookup.

### Tool Inventory (36 tools)

**Project (5 tools)**:
`create_project`, `update_project`, `archive_project`, `list_projects`, `get_project_details`

`list_projects` returns lightweight `id, name, status` per project. `get_project_details` accepts a `detail_level` parameter (`summary`, `status`, `metrics`) to control output depth.

**Requirements (8 tools)**:
`create_requirement`, `update_requirement`, `update_requirement_status`, `archive_requirement`, `query_requirements`, `get_requirement_details`, `batch_create_requirements`, `clone_requirement`

`query_requirements` supports `output_format` (`summary`, `json`, `markdown`), `limit`, and `offset`. `get_requirement_details` accepts a `trace` boolean to include parent/child requirements.

**Tasks (8 tools)**:
`create_task`, `update_task`, `update_task_status`, `archive_task`, `query_tasks`, `get_task_details`, `batch_create_tasks`, `clone_task`

`query_tasks` supports `output_format`, `limit`, `offset`. `get_task_details` accepts a `sections` array (`planning`, `execution`, `requirements`, `adrs`, `subtasks`); default is `["planning", "requirements"]`.

**Architecture (7 tools)**:
`create_architecture_decision`, `update_architecture_decision`, `update_architecture_status`, `archive_architecture_decision`, `query_architecture_decisions`, `get_architecture_details`, `add_architecture_review`

`query_architecture_decisions` supports `output_format`, `limit`, `offset`.

**Relationships (3 tools)**:
`create_relationship`, `delete_relationship`, `query_relationships`

`query_relationships` supports `entity_id` filter, `output_format` (`summary`, `json`), `limit`, `offset`.

**Validation (2 tools)**:
`validate_project_plan`, `get_valid_status_transitions`

`validate_project_plan` defaults to `summary_only=true` (returns counts only). Pass `summary_only=false` for the full issue list.

**Export (2 tools)**:
`export_project_documentation`, `create_architectural_diagrams`

Both tools require `output_directory` / `output_path` to be specified — they write to disk and return only file paths (no inline content).

**Status (1 tool)**:
`diff_project`

## State Machines

### Requirement Lifecycle

```
Draft -> Under Review -> Approved -> Architecture -> Ready -> Implemented -> Validated
                  |           |                                      |
                  v           v                                      v
              Deprecated  Deprecated                            Deprecated
```

Valid transitions:
- **Draft**: Under Review, Deprecated
- **Under Review**: Draft, Approved, Deprecated
- **Approved**: Architecture, Ready, Deprecated
- **Architecture**: Ready, Approved
- **Ready**: Implemented, Deprecated
- **Implemented**: Validated, Ready
- **Validated**: Deprecated
- **Deprecated**: (terminal)

### Task Lifecycle

```
Not Started -> In Progress -> Complete
                    |
                    v
                 Blocked -> In Progress
                    |
                    v
                Abandoned
```

Valid transitions:
- **Not Started**: In Progress, Abandoned
- **In Progress**: Complete, Blocked, Abandoned
- **Blocked**: In Progress, Abandoned
- **Complete**: (terminal)
- **Abandoned**: (terminal)

### Architecture Decision Lifecycle

```
Draft -> Under Review -> Proposed -> Accepted -> Implemented
  |              |              |
  |              v              v
  |           Approved       Rejected
  |              |              |
  +-----> Accepted (shortcut)   v
                            Deprecated
```

Valid transitions:
- **Draft**: Under Review, **Accepted** (shortcut), Deprecated
- **Under Review**: Proposed, Approved, **Accepted** (shortcut), Deprecated
- **Proposed**: Accepted, Rejected, Deprecated
- **Accepted**: Implemented, Deprecated
- **Rejected**: Deprecated
- **Approved**: Implemented, Deprecated
- **Implemented**: Deprecated
- **Deprecated**: (terminal)

The `Draft -> Accepted` and `Under Review -> Accepted` shortcuts allow fast-tracking simple ADRs without requiring intermediate review steps.

Supersession is expressed via `superseded_by` FK + `status='Deprecated'`.

## Soft Delete (Archive)

All primary entities (projects, requirements, tasks, architecture) support soft deletion via:
- `is_archived INTEGER DEFAULT 0` -- flag
- `archived_at TEXT` -- timestamp when archived

Archived entities are hidden from queries by default. Pass `include_archived=true` to retrieve them.

## Transport Layer

The server supports three MCP transport modes:

| Transport | Protocol | Default Port | Use Case |
|-----------|----------|-------------|----------|
| stdio | stdin/stdout | N/A | Direct CLI integration (default) |
| streamable-http | HTTP POST | 8080 | Network access, recommended |
| sse | HTTP GET+POST | 8080 | Legacy browser/network clients |

### Configuration

CLI arguments take precedence over environment variables:
- `--transport` / `LIFECYCLE_TRANSPORT` (default: `stdio`)
- `--host` / `LIFECYCLE_HOST` (default: `127.0.0.1`)
- `--port` / `LIFECYCLE_PORT` (default: `8080`)

### Security

When binding to `0.0.0.0`, DNS-rebinding protection is disabled to allow LAN clients. For other bind addresses, protection is enabled with explicit allowed hosts/origins.

## Database Manager

`DatabaseManager` provides an async connection pool backed by `aiosqlite`:

- **Pool size**: 5 connections (configurable)
- **Connection checkout**: Bounded by `asyncio.Semaphore` to prevent over-subscription
- **FK enforcement**: `PRAGMA foreign_keys=ON` re-applied on every checkout
- **Write transactions**: `BEGIN IMMEDIATE` for atomic writes with automatic rollback on failure
- **Retry logic**: Configurable retries with exponential backoff for `database is locked` errors
- **Schema init**: v2 schema applied automatically on first run if `projects` table does not exist

### Connection Lifecycle

```
initialize() -> creates pool -> applies schema if needed
get_connection() -> acquires semaphore -> borrows from queue -> enforces FKs -> yields -> returns to queue
transaction() -> get_connection() -> BEGIN IMMEDIATE -> yields -> COMMIT/ROLLBACK
close() -> drains queue -> closes all connections
```

## Project Structure

```
src/lifecycle_mcp/
    __init__.py
    server.py                    # MCP server, handler registry, transport
    database_manager.py          # Async SQLite pool
    lifecycle-schema-v2.sql      # Database schema
    constants.py                 # Shared state machines, relationship rules, entity-table map
    handlers/
        __init__.py              # Handler exports
        base_handler.py          # Abstract base class
        project_handler.py       # Project CRUD (5 tools)
        requirement_handler.py   # Requirement lifecycle (8 tools)
        task_handler.py          # Task management (8 tools)
        architecture_handler.py  # ADR management (7 tools)
        relationship_handler.py  # Entity relationships (3 tools)
        validation_handler.py    # Plan validation (2 tools)
        export_handler.py        # Documentation export (2 tools)
        status_handler.py        # Project diffs (1 tool)
tests/
    conftest.py                  # Shared fixtures
    test_server_integration.py   # Server integration tests (36 tools, 8 handlers)
    test_constants.py            # Constants module tests
    test_*.py                    # Domain-specific handler tests
```
