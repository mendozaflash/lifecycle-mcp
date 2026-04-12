# Planning Guidelines (MCP-Assisted)

## Purpose

This file defines how to plan software projects using the **lifecycle-mcp** MCP server as the single source of truth for requirements, tasks, architecture decisions (ADRs), and their relationships. There are no `SPEC.md`, `FEATURE_PLAN.md`, or `STATUS.md` files — the MCP database holds all structured planning data.

The MCP server stores requirements with acceptance criteria, tasks with technical outlines, and ADRs with decision drivers in a SQLite database. Agents interact with it via 36 MCP tools. The planning agent's job is to populate that database correctly and produce a minimal execution manifest — a short markdown file listing task IDs and batch order.

### What the MCP Manages

| Concern | MCP tool |
|---------|----------|
| Requirements, acceptance criteria, scope | `batch_create_requirements` with structured fields per requirement |
| Implementation tasks, dependency map, DoD | `batch_create_tasks` with technical outlines + `create_relationship` for dependency chains |
| Architecture decisions | `create_architecture_decision` with decision drivers, alternatives, consequences |
| Coherence validation | `validate_project_plan` detects orphans, cycles, missing fields, unlinked ADRs |
| Status and metrics | `get_project_details` with `detail_level=status` or `metrics` |

### Output: The Execution Manifest

Planning produces a **single minimal markdown file** — the execution manifest. It contains only the project ID, task IDs, and batch structure. All structured data (specs, acceptance criteria, technical outlines, relationships) lives in the MCP database, not in files.

```markdown
# Execution: <Phase Name>

Project: PROJ-XXXX

## Tasks (execution order)

### Batch 1
- TASK-0001: Schema + DatabaseManager foundation

### Batch 2
- TASK-0002: BaseHandler validation methods

### Batch 3 (parallel)
- TASK-0004: RequirementHandler rewrite
- TASK-0005: TaskHandler rewrite
- TASK-0006: ArchitectureHandler rewrite
```

No `SPEC.md`. No `FEATURE_PLAN.md`. No `STATUS.md`. The MCP is the source of truth.

---

## Required Skills

| Skill | When to invoke |
|-------|---------------|
| `superpowers:brainstorming` | Before creating requirements in the MCP. Explore requirements, trade-offs, and alternatives first. |
| `superpowers:writing-plans` | Before creating tasks in the MCP. Decompose requirements into ordered, dependency-mapped tasks. |
| `lifecycle-mcp` | Throughout planning. This skill documents the MCP tool API. |

---

## Planning Workflow

### Step 1 — Create or Select a Project

Every entity in the MCP is scoped to a project. Start by creating one or selecting an existing one.

```
create_project(
  name="Phase Name",
  description="Brief purpose statement",
  tech_stack=["Python", "SQLite", ...],
  constraints=["No breaking changes", ...]
)
```

Record the returned `PROJ-XXXX` ID — all subsequent calls require it.

### When a Design Spec Already Exists

If a detailed design document already exists (e.g., in `project_management/phases/`), skip the brainstorming step (Step 2). Instead, translate the design directly into MCP requirements (Step 3). Present a summary of the design to the user for approval before proceeding.

### Step 2 — Brainstorm Requirements

Invoke `superpowers:brainstorming` to explore requirements before committing them to the MCP.

Focus the brainstorming on:
- What problem each requirement solves (current state → desired state)
- Acceptance criteria (concrete, testable)
- Priority (P0 = must-have, P1 = should-have, P2 = nice-to-have, P3 = future)
- Scope boundaries (what's explicitly out)

### Step 3 — Create Requirements in the MCP

Use `batch_create_requirements` to create all requirements atomically. Each requirement should include:

| Field | Purpose | Required |
|-------|---------|----------|
| `title` | Short descriptive name | Yes |
| `type` | FUNC, NFUNC, TECH, BUS, INTF | Yes |
| `priority` | P0-P3 | Yes |
| `current_state` | What exists today | Recommended |
| `desired_state` | What should exist after | Recommended |
| `functional_requirements` | Bullet list of capabilities | Recommended |
| `acceptance_criteria` | Testable conditions for "done" | Yes |
| `out_of_scope` | Explicit exclusions | Recommended |
| `business_value` | Why this matters | Optional |

**Rules:**
- Every requirement needs acceptance criteria. A requirement without acceptance criteria cannot be validated.
- Use `current_state` / `desired_state` to capture the transformation, not just the end state.
- Keep requirements atomic — one concern per requirement. If a requirement has 8+ acceptance criteria, it's likely two requirements.

### Step 4 — Approve Requirements

Requirements start as "Under Review". Move them to "Approved" before creating tasks:

```
update_requirement_status(requirement_id="REQ-XXXX", new_status="Approved")
```

**Important:** Tasks cannot be approved unless their linked requirements are already Approved. The MCP enforces this — plan your approval sequence accordingly.

### Step 5 — Record Architecture Decisions

For any non-obvious technical decisions, create ADRs:

```
create_architecture_decision(
  project_id="PROJ-XXXX",
  title="Decision Title",
  context="Why this decision is needed",
  decision="What was decided",
  decision_drivers=["Driver 1", "Driver 2"],
  considered_options=["Option A", "Option B", "Option C"],
  consequences={"positive": [...], "negative": [...]}
)
```

ADRs can be linked to requirements via `create_relationship` with type `addresses` or `informs`. Use `addresses` when an ADR directly resolves a requirement, and `informs` when an ADR provides context or constraints that shape a requirement.

**Status shortcuts:** ADRs support `Draft → Accepted` as a direct transition (skipping Under Review/Proposed) for straightforward decisions.

### Step 6 — Validate Requirements Against Architecture (Subagent)

Before creating tasks, dispatch a **requirements-architecture coherence subagent** to verify that the requirements and ADRs form a consistent, complete foundation. This catches gaps early — before task decomposition bakes them in.

**Dispatch the subagent with this prompt:**

```
Validate requirements and architecture decisions for project PROJ-XXXX.

Steps:
1. Call query_requirements(project_id="PROJ-XXXX", output_format="json") to get all requirements.
2. Call query_architecture_decisions(project_id="PROJ-XXXX", output_format="json") to get all ADRs.
3. Call query_relationships(project_id="PROJ-XXXX") to get all relationships.

Then check the following:

COVERAGE CHECKS:
- Every TECH requirement should have at least one ADR that addresses it
  (relationship: ADR -> REQ with type "addresses"). List any unaddressed TECH requirements.
- Every ADR should link to at least one requirement. List any orphan ADRs.
- P0 requirements must have acceptance criteria with at least 2 items. Flag any that don't.

CONSISTENCY CHECKS:
- Do any two requirements have contradicting scope boundaries
  (one's in-scope overlaps another's out-of-scope)? List conflicts.
- Do any ADR consequences.negative items directly conflict with a requirement's
  desired_state or acceptance_criteria? List conflicts.
- Are there requirements that should depend on each other but don't have a
  "depends" or "refines" relationship? Suggest missing relationships.

COMPLETENESS CHECKS:
- Are there implicit requirements that the ADRs assume but no explicit requirement
  captures? (e.g., an ADR assumes FK enforcement exists but no requirement states it.)
- Do all requirements have both current_state and desired_state filled in?
  List any missing.

Return a structured report:
  PASS/FAIL (FAIL if any coverage or consistency issue found)
  COVERAGE: [list of issues or "all clear"]
  CONSISTENCY: [list of issues or "all clear"]
  COMPLETENESS: [list of issues or "all clear"]
  SUGGESTED FIXES: [concrete actions — create relationship, add acceptance criteria, etc.]
```

**On PASS:** Proceed to Step 7 (task decomposition).

**On FAIL:** Fix the issues listed in the report before proceeding. Common fixes:
- Create missing `addresses` relationships between ADRs and requirements.
- Add acceptance criteria to under-specified P0 requirements.
- Create missing requirements that ADRs implicitly assume.
- Add `depends` relationships between requirements with ordering constraints.

Re-run the subagent after fixes to confirm PASS.

### Step 7 — Decompose into Tasks

Invoke `superpowers:writing-plans` before creating tasks. Then use `batch_create_tasks`:

Each task should include:

| Field | Purpose | Required |
|-------|---------|----------|
| `title` | Feature ID + description (e.g., "DB-01: Schema foundation") | Yes |
| `priority` | P0-P3 | Yes |
| `effort` | XS, S, M, L, XL | Recommended |
| `user_story` | "As a..., I need... so that..." | Recommended |
| `acceptance_criteria` | Testable conditions | Yes |
| `scope_boundaries` | In-scope and out-of-scope | Recommended |
| `technical_outline` | Implementation steps | Yes |
| `files_touched` | File paths this task modifies/creates | Yes |
| `verification_commands` | Exact test commands | Yes |
| `risk_notes` | Known risks or blockers | Optional |

**Task field design:**
- `technical_outline` is the task's spec — the complete implementation plan. Keep it concrete: numbered steps, file paths, function names. The subagent pulls this directly from the MCP.
- `files_touched` scopes the subagent's work. It should not modify files outside this list. If a file outside `files_touched` must change (e.g., a test with a hardcoded count that breaks due to a constant change), the subagent should include it and record the addition in `deviation_from_plan`. Prefer listing such files proactively during planning by checking for hardcoded assertions referencing modified constants or symbols.
- `verification_commands` must be explicit: `uv run pytest tests/test_foo.py -v`, not "run the tests".
- `acceptance_criteria` on a task = the task's Definition of Done. The subagent uses this to determine when it's finished.

### Step 8 — Establish Relationships

Create two types of relationships:

**Task → Requirement (implements):**
```
create_relationship(
  source_id="TASK-0001", target_id="REQ-0001",
  relationship_type="implements", project_id="PROJ-XXXX"
)
```

**Task → Task (depends):**
```
create_relationship(
  source_id="TASK-0002", target_id="TASK-0001",
  relationship_type="depends", project_id="PROJ-XXXX"
)
```

**Rules for dependency relationships:**
- List only **direct** dependencies, not transitive ones. If TASK-0003 depends on TASK-0002 which depends on TASK-0001, TASK-0003 should only declare a dependency on TASK-0002.
- Reverse the dependency direction only if you mean "blocks": `source depends on target` means source cannot start until target is done.

**Other useful relationship types:**
- `addresses` — task or ADR addresses (but doesn't fully implement) a requirement
- `informs` — entity informs another entity (architecture ↔ requirement, architecture ↔ task, task ↔ task)
- `blocks` — entity blocks another entity
- `parent` — hierarchical grouping (requirement → requirement only)
- `refines` — entity refines another entity (requirement → requirement only)

**Valid relationship combinations** are defined in `src/lifecycle_mcp/constants.py:VALID_RELATIONSHIP_COMBINATIONS`. Always check this when unsure whether a source→target→type triple is allowed. The server will reject invalid combinations with a clear error message.

### Step 9 — Final Coherence Validation (Subagent)

After all tasks, relationships, and dependency chains are in place, dispatch a **final coherence validation subagent** to run the full suite of automated and manual checks. This is the planning gate — nothing proceeds to approval until this passes.

**Dispatch the subagent with this prompt:**

```
Run final coherence validation for project PROJ-XXXX.

Steps:
1. Call validate_project_plan(project_id="PROJ-XXXX", summary_only=false).
   Record all errors and warnings.

2. Call query_tasks(project_id="PROJ-XXXX", output_format="json") to get all tasks.
   Call query_relationships(project_id="PROJ-XXXX") to get all relationships.

3. Run the following checks:

AUTOMATED (from validate_project_plan):
- Orphan requirements (no linked tasks) — ERROR
- Orphan tasks (no linked requirements) — ERROR
- Dependency cycles — ERROR
- Missing fields (acceptance_criteria, verification_commands, files_touched) — WARNING
- Unlinked ADRs — WARNING

FILE OVERLAP CHECK:
- For each pair of tasks, compare their files_touched lists.
  If two tasks modify the same file, verify one depends on the other.
  List any overlapping tasks without a dependency relationship. — ERROR

SCOPE CONTRADICTION CHECK:
- For each pair of tasks, compare their scope_boundaries.
  Flag any case where one task's in-scope work contradicts another
  task's out-of-scope declaration. — ERROR

ARCHITECTURE COMPLIANCE CHECK:
- Call query_architecture_decisions(project_id="PROJ-XXXX", output_format="json").
- For each Accepted ADR, verify at least one task's technical_outline
  references or implements the decision. If an ADR's decision is ignored
  by all task outlines, flag it. — WARNING
- Check that no task's technical_outline contradicts an Accepted ADR's
  decision (e.g., task says "use UUID" but ADR says "use sequential IDs"). — ERROR

DEPENDENCY COMPLETENESS CHECK:
- Verify the dependency graph forms a valid DAG (no cycles — already checked).
- Verify that root tasks (no dependencies) don't implicitly assume
  work from non-root tasks exists. Compare each root task's
  technical_outline against symbols/files produced by other tasks.
  Flag implicit dependencies. — WARNING

Return a structured report:
  OVERALL: PASS/FAIL (FAIL if any ERROR found)
  ERRORS: [numbered list with entity IDs and descriptions, or "none"]
  WARNINGS: [numbered list with entity IDs and descriptions, or "none"]
  FILE OVERLAPS: [list of (TASK-A, TASK-B, file) triples, or "none"]
  SCOPE CONTRADICTIONS: [list of (TASK-A, TASK-B, conflict) triples, or "none"]
  IGNORED ADRs: [list of ADR IDs with their decisions, or "none"]
  IMPLICIT DEPENDENCIES: [list of (TASK-A, TASK-B, reason) triples, or "none"]
  SUGGESTED FIXES: [concrete actions for each issue]
```

**On PASS:** Proceed to Step 10 (approve tasks).

**On FAIL:** Fix all errors before proceeding:
- File overlaps → add a `depends` relationship between the two tasks (the one that writes first should be the dependency target).
- Scope contradictions → update `scope_boundaries` on one or both tasks to resolve the conflict.
- Ignored ADRs → update the relevant task's `technical_outline` to reference the ADR decision, or create an `informs` relationship.
- Implicit dependencies → add the missing `depends` relationship.

Re-run the subagent after fixes to confirm PASS. Warnings are acceptable but should be reviewed.

### Step 10 — Approve Tasks

Move tasks to "Approved" to signal they're ready for implementation:

```
update_task_status(task_id="TASK-XXXX", new_status="Approved")
```

**Prerequisite:** All requirements linked via `implements` relationships must already be "Approved". The MCP enforces this constraint.

Approve tasks in dependency order — leaf tasks first, then their dependents.

### Step 11 — Produce the Execution Manifest

The final planning output is a minimal markdown file listing the project ID, task IDs, and batch structure. Derive batches from the dependency relationships:

```
query_tasks(project_id="PROJ-XXXX", status="Approved", output_format="json")
query_relationships(project_id="PROJ-XXXX", relationship_type="depends")
```

Write the manifest:

```markdown
# Execution: <Phase Name>

Project: PROJ-XXXX

## Tasks (execution order)

### Batch 1
- TASK-0001: <title>

### Batch 2
- TASK-0002: <title>

### Batch 3 (parallel)
- TASK-0004: <title>
- TASK-0005: <title>
```

This file is the handoff to the execution agent. Everything else lives in the MCP.

---

## Coherence Review Summary

Planning uses two subagent-driven validation gates that combine MCP automated checks with semantic analysis:

| Gate | Step | What it catches |
|------|------|-----------------|
| Requirements-Architecture Coherence | Step 6 | Unaddressed TECH requirements, orphan ADRs, scope contradictions between requirements, ADR consequences conflicting with requirements, implicit missing requirements |
| Final Coherence Validation | Step 9 | Orphan entities, dependency cycles, file overlaps between tasks, scope contradictions between tasks, ignored ADR decisions, implicit dependencies, missing fields |

Both gates produce structured PASS/FAIL reports with concrete fix suggestions. All errors must be resolved before proceeding. Warnings should be reviewed but do not block.

---

## Batch Execution Order Derivation

The dependency relationships in the MCP directly encode the execution order. To derive batches:

1. Query all tasks: `query_tasks(project_id="PROJ-XXXX", output_format="json")`
2. Query all dependency relationships: `query_relationships(project_id="PROJ-XXXX", relationship_type="depends")`
3. Group tasks into batches:
   - **Batch 1:** Tasks with no `depends` relationships (roots)
   - **Batch 2:** Tasks whose dependencies are all in Batch 1
   - **Batch N:** Tasks whose dependencies are all in Batches 1..N-1
4. Tasks within the same batch can be executed in parallel.

The MCP stores this information structurally — no dependency map table in a markdown file needed.

---

## State Machine Reference

### Requirements
```
Under Review → Approved → [auto-progresses based on task status]
  → Partially Implemented → Partially Implemented Validated
  → Implemented → Partially Validated → Validated
  → Deprecated (from any state)
```

Requirements auto-progress when their linked tasks change status. You rarely need to manually advance a requirement past "Approved".

**Auto-progression rules:**
- When **some** linked tasks are Implemented → requirement moves to **Partially Implemented**
- When **all** linked tasks are Implemented → requirement moves to **Implemented**
- When **some** linked tasks are Validated → requirement moves to **Partially Validated** (or **Partially Implemented Validated** if not all implemented yet)
- When **all** linked tasks are Validated → requirement moves to **Validated**

**Note:** When parallel agents update tasks linked to the same requirement simultaneously, each agent may see different intermediate states due to race conditions. This is expected — each agent's view is a snapshot. The final state will be correct once all parallel updates complete.

### Tasks
```
Under Review → Approved → Implemented → Validated → Deprecated
```

### Architecture Decisions
```
Draft → Under Review → Proposed → Accepted → Deprecated
Shortcuts: Draft → Accepted (direct)
```

Use `get_valid_status_transitions(entity_type, current_status)` if unsure.

---

## Definition of Planning Complete

Planning is complete when:

1. All requirements are created with acceptance criteria and moved to "Approved"
2. All ADRs are created, linked to requirements, and accepted
3. **Requirements-architecture coherence subagent** (Step 6) returned PASS
4. All tasks are created with technical outlines, acceptance criteria, files_touched, and verification_commands
5. All task → requirement `implements` relationships exist
6. All task → task `depends` relationships exist (dependency chain)
7. **Final coherence validation subagent** (Step 9) returned PASS — 0 errors, no file overlaps, no scope contradictions, no ignored ADRs
8. All tasks are moved to "Approved"
9. `get_project_details(detail_level="status")` shows the expected entity counts
10. The execution manifest (minimal MD with project ID, task list, batch structure) is written

---

## Quick Reference

| Goal | Tool |
|------|------|
| Start a project | `create_project` |
| Add requirements | `batch_create_requirements` |
| Approve a requirement | `update_requirement_status` |
| Record a technical decision | `create_architecture_decision` |
| Add implementation tasks | `batch_create_tasks` |
| Link task to requirement | `create_relationship(type="implements")` |
| Set task dependency | `create_relationship(type="depends")` |
| Check plan health | `validate_project_plan` |
| See valid next states | `get_valid_status_transitions` |
| View project summary | `get_project_details` |
| Query by priority | `query_requirements(priority="P0")` |
| View task with context | `get_task_details(sections=["planning","requirements","adrs"])` |
| View requirement trace | `get_requirement_details(trace=true)` |
