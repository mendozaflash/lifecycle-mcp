# Lifecycle MCP Server

A Model Context Protocol (MCP) server for comprehensive software lifecycle management. This server provides structured tracking of requirements, tasks, and architecture decisions through a SQLite database with full traceability and automated state management.

## Features

- **Project Management**: Create and manage projects with full lifecycle tracking
- **Requirements Management**: Create and manage software requirements with validation and lifecycle tracking
- **Task Management**: Track implementation tasks with hierarchical structure and effort estimation
- **Architecture Decisions**: Record ADRs (Architecture Decision Records) with full context and review workflow
- **Relationship Tracking**: Many-to-many relationships between requirements, tasks, and architecture
- **State Validation**: Automatic validation of lifecycle state transitions
- **Batch Operations**: Bulk create requirements and tasks atomically
- **Documentation Export**: Generate Mermaid diagrams and markdown documentation

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/mendozaflash/lifecycle-mcp.git
cd lifecycle-mcp

# 2. Install globally (easiest for using across projects)
pip install -e .

# 3. Go to any project where you want to use lifecycle management
cd /path/to/your/project

# 4. Add the MCP server to Claude
claude mcp add lifecycle lifecycle-mcp -e LIFECYCLE_DB=/path/to/your/project/lifecycle.db

# 5. Start using lifecycle tools in Claude!
```

## Installation Options

### Prerequisites (Optional — Recommended)
Using `uv` (faster Python package manager):
```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Or with homebrew
brew install uv
```

### Clone the Repository
```bash
git clone https://github.com/mendozaflash/lifecycle-mcp.git
cd lifecycle-mcp
```

### Option 1: Install with pip
```bash
# On macOS: plain pip works
pip install -e .

# On Linux/WSL2: system pip is restricted — use a virtual environment (Option 3)
# or use uv (Option 2) instead

# Add to Claude from any project directory
claude mcp add lifecycle lifecycle-mcp -e LIFECYCLE_DB=./lifecycle.db
```

### Option 2: Run from Source with uv (Recommended)
```bash
# From any project directory:
claude mcp add lifecycle $(which uv) -- --directory /path/to/lifecycle-mcp run server.py -e LIFECYCLE_DB=./lifecycle.db
```

### Option 3: Virtual Environment
```bash
cd /path/to/lifecycle-mcp
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e .

# Use the full path when adding to Claude
claude mcp add lifecycle /path/to/venv/bin/lifecycle-mcp -e LIFECYCLE_DB=./lifecycle.db
```

## Docker Deployment

The server ships with a `Dockerfile` and `docker-compose.yml` for containerized use.

### Quick start with docker compose

```bash
# Build and start in the background
docker compose up -d

# View logs
docker compose logs -f lifecycle-mcp

# Stop
docker compose down
```

The container runs `streamable-http` transport on port **8080** and stores the database at `/data/lifecycle.db` inside the container. The `docker-compose.yml` mounts a named volume (`lifecycle-data`) so data persists across container restarts.

### Manual Docker

```bash
# Build the image
docker build -t lifecycle-mcp .

# Run with a persistent volume
docker run -d \
  --name lifecycle-mcp \
  -p 8080:8080 \
  -v lifecycle-data:/data \
  lifecycle-mcp
```

### Connect Claude Code to a running container

```bash
# Streamable HTTP transport (recommended)
claude mcp add lifecycle --transport http http://localhost:8080/mcp/

# SSE transport
claude mcp add lifecycle --transport sse http://localhost:8080/sse
```

> **Note:** The streamable-http endpoint requires a trailing slash: `/mcp/`

## Network Transport

The server supports three transport modes:

```bash
# Streamable HTTP (recommended for network/Docker use)
lifecycle-mcp --transport streamable-http --host 0.0.0.0 --port 8080

# SSE (Server-Sent Events)
lifecycle-mcp --transport sse --host 0.0.0.0 --port 8080

# Stdio (default, for direct MCP client integration)
lifecycle-mcp --transport stdio
```

Environment variable fallbacks:
| Variable | Default | Description |
|---|---|---|
| `LIFECYCLE_TRANSPORT` | `stdio` | Transport type |
| `LIFECYCLE_HOST` | `127.0.0.1` | Bind address |
| `LIFECYCLE_PORT` | `8080` | Port number |
| `LIFECYCLE_DB` | `./lifecycle.db` | Database path |

CLI arguments take precedence over environment variables.

## Manual Configuration (Claude Desktop)

**macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
**Windows**: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "lifecycle": {
      "command": "lifecycle-mcp",
      "env": {
        "LIFECYCLE_DB": "./lifecycle.db"
      }
    }
  }
}
```

For a network server:
```json
{
  "mcpServers": {
    "lifecycle": {
      "type": "http",
      "url": "http://localhost:8080/mcp/"
    }
  }
}
```

## MCP Tools Reference

The server exposes **36 tools** across **8 handler modules**:

### Project Management (5 tools)
| Tool | Description |
|---|---|
| `create_project` | Create a new project |
| `list_projects` | List projects with id, name, and status |
| `get_project_details` | Project metadata, status breakdowns, and metrics (use `detail_level`: `summary`/`status`/`metrics`) |
| `update_project` | Update project name, description, or tech stack |
| `archive_project` | Soft-delete a project and all its children |

### Requirement Management (8 tools)
| Tool | Description |
|---|---|
| `create_requirement` | Create a requirement linked to a project |
| `update_requirement` | Update requirement fields |
| `update_requirement_status` | Move requirement through lifecycle states |
| `query_requirements` | Search and filter requirements |
| `get_requirement_details` | Full requirement with relationships; use `trace=true` for hierarchy |
| `batch_create_requirements` | Create multiple requirements atomically |
| `clone_requirement` | Clone a requirement with a new ID |
| `archive_requirement` | Soft-delete a requirement |

**Requirement lifecycle states:**
`Under Review` → `Approved` → `Partially Implemented` / `Implemented` → `Partially Validated` / `Validated` → `Deprecated`

### Task Management (8 tools)
| Tool | Description |
|---|---|
| `create_task` | Create an implementation task linked to a project |
| `update_task` | Update task planning fields |
| `update_task_status` | Update task status, execution notes, or deviation notes |
| `query_tasks` | Search and filter tasks |
| `get_task_details` | Task details with configurable sections (`planning`, `execution`, `requirements`, `adrs`, `subtasks`) |
| `batch_create_tasks` | Create multiple tasks atomically |
| `clone_task` | Clone a task (optionally with child tasks) |
| `archive_task` | Soft-delete a task |

**Task lifecycle states:** `Under Review` → `Approved` → `Implemented` → `Validated` → `Deprecated`

### Architecture Decisions (7 tools)
| Tool | Description |
|---|---|
| `create_architecture_decision` | Record an ADR linked to a project |
| `update_architecture_decision` | Update ADR fields |
| `update_architecture_status` | Move ADR through status states (supports shortcut: `Draft→Accepted`) |
| `query_architecture_decisions` | Search and filter ADRs |
| `get_architecture_details` | Full ADR with relationships and reviews |
| `add_architecture_review` | Add a review comment to an ADR |
| `archive_architecture_decision` | Soft-delete an ADR |

**ADR states:** `Draft` → `Under Review` → `Proposed` → `Accepted` / `Rejected` → `Deprecated`

### Relationship Management (3 tools)
| Tool | Description |
|---|---|
| `create_relationship` | Link two entities (`implements`, `addresses`, `depends`, `blocks`, `informs`, `requires`, `parent`, `refines`, `conflicts`, `relates`) |
| `delete_relationship` | Remove a relationship |
| `query_relationships` | Search and filter relationships |

### Validation (2 tools)
| Tool | Description |
|---|---|
| `validate_project_plan` | Check for orphans, cycles, missing fields and invalid states. Use `summary_only=false` for full details |
| `diff_project` | Show entities whose status changed in a time window |

### Documentation Export (2 tools)
| Tool | Description |
|---|---|
| `export_project_documentation` | Export project docs as markdown files to `output_directory` |
| `create_architectural_diagrams` | Generate Mermaid diagrams (`requirements`, `tasks`, `architecture`, `full_project`, `directory_structure`, `dependencies`) to `output_path` |

> **Note:** Both export tools require `output_directory` / `output_path` — they write files, they do not return inline content.

### Status Monitoring (1 tool)
| Tool | Description |
|---|---|
| `get_valid_status_transitions` | Return valid next states for a given entity type and current status |

## Entity ID Formats

All IDs are global sequential integers, zero-padded to 4 digits:

| Entity | Format | Example |
|---|---|---|
| Project | `PROJ-XXXX` | `PROJ-0001` |
| Requirement | `REQ-XXXX` | `REQ-0001` |
| Task | `TASK-XXXX` | `TASK-0001` |
| Architecture | `ADR-XXXX` | `ADR-0001` |

Tasks support hierarchical numbering for subtasks (e.g. `TASK-0001-01`, `TASK-0001-01-02`).

## Database

The server uses SQLite with `aiosqlite` for async access. The schema is auto-initialized on first run.

Key tables:
- **projects** — top-level scoping entity; all other entities reference a `project_id`
- **requirements** — central entity with lifecycle states and metadata
- **tasks** — implementation work items with effort estimation and assignee tracking
- **architecture** — ADRs with decision drivers, considered options, and consequences
- **relationships** — single polymorphic table linking any two entities
- **reviews** — comments and feedback on ADRs
- **lifecycle_events** — automatic audit log of all status changes

Foreign key enforcement is on (`PRAGMA foreign_keys=ON`) for every connection.

**Reset the database:**
```bash
rm lifecycle.db  # or the path set in LIFECYCLE_DB
# It will be recreated on next server start
```

## Development

### Using uv (Recommended)
```bash
# Install dependencies
uv sync

# Run the server directly
uv run server.py

# Add to Claude Code for local development
claude mcp add lifecycle $(which uv) -- --directory $(pwd) run server.py
```

### Using pip
```bash
pip install -e .
lifecycle-mcp
claude mcp add lifecycle lifecycle-mcp
```

### Running Tests
```bash
uv run pytest tests/ --tb=short
```

## Building Desktop Extension (.dxt)

To create a Desktop Extension package for one-click installation in Claude Desktop:

```bash
# Build the .dxt file
make build-dxt
# or
python3 build_dxt.py
```

This creates `lifecycle-mcp-1.0.0.dxt` which users can double-click to install in Claude Desktop.

## Troubleshooting

### "MCP error -32000: Connection closed"
1. Re-install the package: `pip install -e .`
2. Re-add the server: `claude mcp add lifecycle lifecycle-mcp`
3. Verify the server starts cleanly: `lifecycle-mcp`

### "Server Not Found"
1. Verify `lifecycle-mcp` is on your `PATH`: `which lifecycle-mcp`
2. Check `pyproject.toml` has the `[project.scripts]` entry point
3. Reinstall: `pip install -e .`

### Database Lock Errors
Ensure only one instance of the server is running and that the database file has write permissions.

### Streamable-HTTP 404 / Connection Refused
- Confirm the endpoint includes a trailing slash: `http://host:8080/mcp/`
- Check the server is running on the expected host/port: `lifecycle-mcp --transport streamable-http --host 0.0.0.0 --port 8080`

## Usage Examples

For detailed workflow examples, see [USAGE_EXAMPLES.md](USAGE_EXAMPLES.md).
