-- Lifecycle MCP v2 Schema
-- Clean-sheet design: project-scoped entities, sequential IDs, polymorphic relationships
-- Replaces the legacy schema entirely.

-- ============================================================
-- ID Generation
-- ============================================================

CREATE TABLE sequences (
    entity_type TEXT PRIMARY KEY,  -- 'requirement', 'task', 'architecture', 'project'
    next_val INTEGER NOT NULL DEFAULT 1
);

INSERT INTO sequences (entity_type) VALUES ('requirement'), ('task'), ('architecture'), ('project'), ('architectural_pattern');

-- ============================================================
-- Projects — first-class project entity
-- ============================================================

CREATE TABLE projects (
    id TEXT PRIMARY KEY,           -- PROJ-XXXX
    name TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'archived')),
    tech_stack TEXT,               -- JSON array
    constraints TEXT,              -- JSON array
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Requirements — project-scoped
-- ============================================================

CREATE TABLE requirements (
    id TEXT PRIMARY KEY,           -- REQ-XXXX
    project_id TEXT NOT NULL REFERENCES projects(id),
    type TEXT NOT NULL CHECK(type IN ('FUNC', 'NFUNC', 'TECH', 'BUS', 'INTF')),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Under Review' CHECK(status IN ('Under Review', 'Approved', 'Partially Implemented', 'Partially Implemented Validated', 'Implemented', 'Partially Validated', 'Validated', 'Deprecated')),
    priority TEXT NOT NULL CHECK(priority IN ('P0', 'P1', 'P2', 'P3')),
    current_state TEXT,
    desired_state TEXT,
    functional_requirements TEXT,  -- JSON array
    nonfunctional_requirements TEXT, -- JSON array
    out_of_scope TEXT,             -- JSON array
    acceptance_criteria TEXT,      -- JSON array
    business_value TEXT,
    author TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Tasks — project-scoped with planning + execution fields
-- ============================================================

CREATE TABLE tasks (
    id TEXT PRIMARY KEY,           -- TASK-XXXX
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Under Review' CHECK(status IN ('Under Review', 'Approved', 'Implemented', 'Validated', 'Deprecated')),
    priority TEXT NOT NULL CHECK(priority IN ('P0', 'P1', 'P2', 'P3')),
    effort TEXT CHECK(effort IN ('XS', 'S', 'M', 'L', 'XL')),
    user_story TEXT,
    acceptance_criteria TEXT,      -- JSON array
    assignee TEXT,
    parent_task_id TEXT REFERENCES tasks(id),
    -- Planning fields (named columns)
    scope_boundaries TEXT,
    technical_outline TEXT,
    files_touched TEXT,            -- JSON array
    verification_commands TEXT,    -- JSON array
    public_symbols TEXT,           -- JSON array
    risk_notes TEXT,
    -- Execution fields
    execution_notes TEXT,
    deviation_from_plan TEXT,
    completed_at TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Architecture — ADRs with project scoping
-- ============================================================

CREATE TABLE architecture (
    id TEXT PRIMARY KEY,           -- ADR-XXXX
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Under Review' CHECK(status IN ('Under Review', 'Proposed', 'Accepted', 'Rejected', 'Deprecated')),
    context TEXT,
    decision TEXT,
    decision_drivers TEXT,         -- JSON array
    considered_options TEXT,       -- JSON array
    consequences TEXT,             -- JSON object
    authors TEXT,                  -- JSON array
    superseded_by TEXT REFERENCES architecture(id),
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Architectural Patterns — reusable cross-ADR patterns
-- ============================================================

CREATE TABLE architectural_patterns (
    id         TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    name       TEXT NOT NULL,
    type       TEXT NOT NULL CHECK(type IN (
        'database', 'api', 'transport', 'adapter', 'auth', 'schema', 'messaging', 'ui',
        'reliability', 'modularity', 'performance', 'security',
        'scalability', 'testability', 'observability'
    )),
    description TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE adr_patterns (
    adr_id     TEXT NOT NULL REFERENCES architecture(id),
    pattern_id TEXT NOT NULL REFERENCES architectural_patterns(id),
    role       TEXT NOT NULL DEFAULT 'follows'
                   CHECK(role IN ('establishes', 'follows', 'refines')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (adr_id, pattern_id)
);

-- ============================================================
-- Relationships — single polymorphic table
-- ============================================================

CREATE TABLE relationships (
    id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL CHECK(relationship_type IN ('implements', 'addresses', 'depends', 'blocks', 'informs', 'requires', 'parent', 'refines', 'conflicts', 'relates')),
    project_id TEXT REFERENCES projects(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source_id, target_id, relationship_type)
);

-- ============================================================
-- Reviews — entity-scoped review comments
-- ============================================================

CREATE TABLE reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    reviewer TEXT NOT NULL DEFAULT 'MCP User',
    comment TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Lifecycle Events — status change log
-- ============================================================

CREATE TABLE lifecycle_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    from_value TEXT,
    to_value TEXT,
    actor TEXT NOT NULL DEFAULT 'MCP User',
    project_id TEXT REFERENCES projects(id),
    occurred_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ============================================================
-- Views
-- ============================================================

CREATE VIEW project_summary AS
SELECT
    p.id, p.name, p.status,
    (SELECT COUNT(*) FROM requirements r WHERE r.project_id = p.id AND r.is_archived = 0) AS requirement_count,
    (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.is_archived = 0) AS task_count,
    (SELECT COUNT(*) FROM tasks t WHERE t.project_id = p.id AND t.status = 'Validated' AND t.is_archived = 0) AS tasks_completed,
    (SELECT COUNT(*) FROM architecture a WHERE a.project_id = p.id AND a.is_archived = 0) AS adr_count
FROM projects p WHERE p.is_archived = 0;

CREATE VIEW task_hierarchy AS
WITH RECURSIVE task_tree AS (
    SELECT id, title, status, parent_task_id, project_id, 0 AS depth
    FROM tasks WHERE parent_task_id IS NULL AND is_archived = 0
    UNION ALL
    SELECT t.id, t.title, t.status, t.parent_task_id, t.project_id, tt.depth + 1
    FROM tasks t JOIN task_tree tt ON t.parent_task_id = tt.id
    WHERE t.is_archived = 0
)
SELECT * FROM task_tree;

-- ============================================================
-- Triggers: auto-update updated_at
-- ============================================================

CREATE TRIGGER update_projects_timestamp AFTER UPDATE ON projects
BEGIN UPDATE projects SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER update_requirements_timestamp AFTER UPDATE ON requirements
BEGIN UPDATE requirements SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER update_tasks_timestamp AFTER UPDATE ON tasks
BEGIN UPDATE tasks SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER update_architecture_timestamp AFTER UPDATE ON architecture
BEGIN UPDATE architecture SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER update_architectural_patterns_timestamp AFTER UPDATE ON architectural_patterns
BEGIN UPDATE architectural_patterns SET updated_at = datetime('now') WHERE id = NEW.id; END;

CREATE TRIGGER auto_archive_deprecated_adr AFTER UPDATE OF status ON architecture
WHEN NEW.status = 'Deprecated'
BEGIN
    UPDATE architecture SET is_archived = 1, archived_at = datetime('now') WHERE id = NEW.id;
END;

-- ============================================================
-- Triggers: auto-progression (requirement status from task status)
-- ============================================================
-- Consolidated into 2 triggers to avoid SQLite multi-trigger visibility
-- limitation: when multiple AFTER triggers fire on the same event, later
-- triggers do NOT see changes made by earlier triggers to other tables.
-- By combining cascading steps into a single trigger body, each UPDATE
-- sees the effects of prior UPDATEs within the same trigger.

-- Trigger 1: Task reaches Implemented → cascade requirement through
-- Approved → Partially Implemented → Implemented
CREATE TRIGGER auto_progress_requirement_on_task_implemented AFTER UPDATE OF status ON tasks
WHEN NEW.status = 'Implemented'
BEGIN
    -- Step 1: Approved → Partially Implemented (first task implemented)
    UPDATE requirements SET status = 'Partially Implemented'
    WHERE status = 'Approved'
      AND id IN (
          SELECT rel.target_id FROM relationships rel
          WHERE rel.source_type = 'task' AND rel.source_id = NEW.id
            AND rel.target_type = 'requirement' AND rel.relationship_type = 'implements'
          UNION
          SELECT rel.source_id FROM relationships rel
          WHERE rel.target_type = 'task' AND rel.target_id = NEW.id
            AND rel.source_type = 'requirement' AND rel.relationship_type = 'implements'
      );

    -- Step 2: Partially Implemented → Implemented (all non-deprecated tasks done)
    -- This sees step 1's changes because both UPDATEs are in the same trigger body.
    UPDATE requirements SET status = 'Implemented'
    WHERE status = 'Partially Implemented'
      AND id IN (
          SELECT rel.target_id FROM relationships rel
          WHERE rel.source_type = 'task' AND rel.source_id = NEW.id
            AND rel.target_type = 'requirement' AND rel.relationship_type = 'implements'
          UNION
          SELECT rel.source_id FROM relationships rel
          WHERE rel.target_type = 'task' AND rel.target_id = NEW.id
            AND rel.source_type = 'requirement' AND rel.relationship_type = 'implements'
      )
      AND NOT EXISTS (
          SELECT 1 FROM tasks t WHERE t.id IN (
              SELECT rel2.source_id FROM relationships rel2
              WHERE rel2.target_type = 'requirement' AND rel2.target_id = requirements.id
                AND rel2.source_type = 'task' AND rel2.relationship_type = 'implements'
              UNION
              SELECT rel2.target_id FROM relationships rel2
              WHERE rel2.source_type = 'requirement' AND rel2.source_id = requirements.id
                AND rel2.target_type = 'task' AND rel2.relationship_type = 'implements'
          ) AND t.status != 'Deprecated'
            AND t.status NOT IN ('Implemented', 'Validated')
      );
END;

-- Trigger 2: Task reaches Validated → cascade requirement through
-- PI → PIV, Implemented → PV → Validated, PIV → Validated
CREATE TRIGGER auto_progress_requirement_on_task_validated AFTER UPDATE OF status ON tasks
WHEN NEW.status = 'Validated'
BEGIN
    -- Step 1: Partially Implemented → Partially Implemented Validated
    -- (task validated while other tasks still not implemented)
    -- Must come before steps 3-4 so PI doesn't skip through to Validated.
    UPDATE requirements SET status = 'Partially Implemented Validated'
    WHERE status = 'Partially Implemented'
      AND id IN (
          SELECT rel.target_id FROM relationships rel
          WHERE rel.source_type = 'task' AND rel.source_id = NEW.id
            AND rel.target_type = 'requirement' AND rel.relationship_type = 'implements'
          UNION
          SELECT rel.source_id FROM relationships rel
          WHERE rel.target_type = 'task' AND rel.target_id = NEW.id
            AND rel.source_type = 'requirement' AND rel.relationship_type = 'implements'
      );

    -- Step 2: Implemented → Partially Validated (first task validated)
    -- Must come before step 3 so the cascade PV → Validated can fire.
    UPDATE requirements SET status = 'Partially Validated'
    WHERE status = 'Implemented'
      AND id IN (
          SELECT rel.target_id FROM relationships rel
          WHERE rel.source_type = 'task' AND rel.source_id = NEW.id
            AND rel.target_type = 'requirement' AND rel.relationship_type = 'implements'
          UNION
          SELECT rel.source_id FROM relationships rel
          WHERE rel.target_type = 'task' AND rel.target_id = NEW.id
            AND rel.source_type = 'requirement' AND rel.relationship_type = 'implements'
      );

    -- Step 3: Partially Validated → Validated (all non-deprecated tasks validated)
    -- Catches both already-PV and those moved to PV by step 2.
    UPDATE requirements SET status = 'Validated'
    WHERE status = 'Partially Validated'
      AND id IN (
          SELECT rel.target_id FROM relationships rel
          WHERE rel.source_type = 'task' AND rel.source_id = NEW.id
            AND rel.target_type = 'requirement' AND rel.relationship_type = 'implements'
          UNION
          SELECT rel.source_id FROM relationships rel
          WHERE rel.target_type = 'task' AND rel.target_id = NEW.id
            AND rel.source_type = 'requirement' AND rel.relationship_type = 'implements'
      )
      AND NOT EXISTS (
          SELECT 1 FROM tasks t WHERE t.id IN (
              SELECT rel2.source_id FROM relationships rel2
              WHERE rel2.target_type = 'requirement' AND rel2.target_id = requirements.id
                AND rel2.source_type = 'task' AND rel2.relationship_type = 'implements'
              UNION
              SELECT rel2.target_id FROM relationships rel2
              WHERE rel2.source_type = 'requirement' AND rel2.source_id = requirements.id
                AND rel2.target_type = 'task' AND rel2.relationship_type = 'implements'
          ) AND t.status != 'Deprecated'
            AND t.status != 'Validated'
      );

    -- Step 4: Partially Implemented Validated → Validated (all non-deprecated tasks validated)
    -- Catches both already-PIV and those moved to PIV by step 1.
    UPDATE requirements SET status = 'Validated'
    WHERE status = 'Partially Implemented Validated'
      AND id IN (
          SELECT rel.target_id FROM relationships rel
          WHERE rel.source_type = 'task' AND rel.source_id = NEW.id
            AND rel.target_type = 'requirement' AND rel.relationship_type = 'implements'
          UNION
          SELECT rel.source_id FROM relationships rel
          WHERE rel.target_type = 'task' AND rel.target_id = NEW.id
            AND rel.source_type = 'requirement' AND rel.relationship_type = 'implements'
      )
      AND NOT EXISTS (
          SELECT 1 FROM tasks t WHERE t.id IN (
              SELECT rel2.source_id FROM relationships rel2
              WHERE rel2.target_type = 'requirement' AND rel2.target_id = requirements.id
                AND rel2.source_type = 'task' AND rel2.relationship_type = 'implements'
              UNION
              SELECT rel2.target_id FROM relationships rel2
              WHERE rel2.source_type = 'requirement' AND rel2.source_id = requirements.id
                AND rel2.target_type = 'task' AND rel2.relationship_type = 'implements'
          ) AND t.status != 'Deprecated'
            AND t.status != 'Validated'
      );
END;

-- ============================================================
-- Triggers: status change logging
-- ============================================================

CREATE TRIGGER log_requirement_status_change AFTER UPDATE OF status ON requirements
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO lifecycle_events (entity_type, entity_id, event_type, from_value, to_value, actor, project_id)
    VALUES ('requirement', NEW.id, 'status_change', OLD.status, NEW.status,
        CASE WHEN (
            (OLD.status = 'Approved' AND NEW.status = 'Partially Implemented') OR
            (OLD.status = 'Partially Implemented' AND NEW.status = 'Implemented') OR
            (OLD.status = 'Partially Implemented' AND NEW.status = 'Partially Implemented Validated') OR
            (OLD.status = 'Implemented' AND NEW.status = 'Partially Validated') OR
            (OLD.status = 'Partially Validated' AND NEW.status = 'Validated') OR
            (OLD.status = 'Partially Implemented Validated' AND NEW.status = 'Validated')
        ) THEN 'system:auto-progression'
        ELSE 'MCP User'
        END,
        NEW.project_id);
END;

CREATE TRIGGER log_task_status_change AFTER UPDATE OF status ON tasks
WHEN OLD.status != NEW.status
BEGIN
    INSERT INTO lifecycle_events (entity_type, entity_id, event_type, from_value, to_value, project_id)
    VALUES ('task', NEW.id, 'status_change', OLD.status, NEW.status, NEW.project_id);
    -- Auto-set completed_at
    UPDATE tasks SET completed_at = CASE WHEN NEW.status = 'Validated' THEN datetime('now') ELSE NULL END WHERE id = NEW.id;
END;

-- ============================================================
-- Indexes
-- ============================================================

CREATE INDEX idx_relationships_source ON relationships(source_type, source_id);
CREATE INDEX idx_relationships_target ON relationships(target_type, target_id);
CREATE INDEX idx_relationships_project ON relationships(project_id);
CREATE INDEX idx_relationships_type ON relationships(relationship_type);
CREATE INDEX idx_requirements_status ON requirements(status);
CREATE INDEX idx_requirements_priority ON requirements(priority);
CREATE INDEX idx_requirements_project ON requirements(project_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_project ON tasks(project_id);
CREATE INDEX idx_architecture_project ON architecture(project_id);
CREATE INDEX idx_adr_patterns_adr ON adr_patterns(adr_id);
CREATE INDEX idx_adr_patterns_pattern ON adr_patterns(pattern_id);
CREATE INDEX idx_adr_patterns_role ON adr_patterns(role);
CREATE INDEX idx_architectural_patterns_project ON architectural_patterns(project_id);
CREATE INDEX idx_architectural_patterns_type ON architectural_patterns(type);
