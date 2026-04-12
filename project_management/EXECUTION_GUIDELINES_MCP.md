# Execution Guidelines (MCP-Assisted)

## Purpose

This file defines how to execute implementation work using the **lifecycle-mcp** MCP server as the single source of truth. The MCP database holds all requirements, tasks, architecture decisions, relationships, and lifecycle state. There are no `SPEC.md`, `FEATURE_PLAN.md`, `STATUS.md`, or Handoff Block files.

The main agent orchestrates. Subagents implement. Each subagent pulls its own task context directly from the MCP — the main agent does not inline specs or compose large prompts. This keeps the main context lightweight and preserves its orchestration capacity across many batches.

---

## Starting Point

Execution begins with a **minimal markdown file** — a simple list of the project ID and task IDs to execute. This is the only file the main agent needs:

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

That's it. All task specifications, acceptance criteria, technical outlines, linked requirements, and ADRs live in the MCP. The file above is a lightweight execution manifest — not a planning document.

---

## Required Skills

| Skill | When to invoke |
|-------|---------------|
| `superpowers:executing-plans` | At session start. |
| `superpowers:test-driven-development` | Each subagent, before writing code. |
| `superpowers:using-git-worktrees` | Before subagent work begins. |
| `superpowers:subagent-driven-development` | Always. Every task is implemented by a subagent. |
| `superpowers:dispatching-parallel-agents` | When a batch contains 2+ independent tasks. |
| `simplify` | Each subagent, after implementation, before verification. |
| `superpowers:verification-before-completion` | Each subagent, before updating task status. |

---

## Role Split

### Main agent responsibilities

1. **Read the execution manifest** — the minimal MD file with project ID and task list.
2. **Derive batch order** — from the manifest or by querying `query_relationships(relationship_type="depends")`.
3. **Dispatch subagents** — one per task, with a short prompt containing only the task ID, project ID, and worktree path.
4. **Track batch completion** — check `get_project_details(detail_level="status")` between batches.
5. **Handle failures** — retry or escalate based on subagent results.

The main agent does **not** read full task details, inline specs, or compose large prompts. It delegates context-loading to the subagent.

### Subagent responsibilities

1. **Pull own context from MCP** — call `get_task_details` with all relevant sections.
2. **Invoke skills** in order: TDD → implement → simplify → verify.
3. **Implement the task** — using the MCP-provided spec, acceptance criteria, and files_touched.
4. **Update MCP status** — call `update_task_status` with execution notes and deviation tracking.
5. **Report back** — return a brief summary to the main agent (pass/fail, files touched, any deviations).

---

## Execution Protocol

### Step 0 — Setup

1. Invoke `superpowers:executing-plans`.
2. Create a git worktree via `superpowers:using-git-worktrees`. Record the worktree path.
3. Verify the project exists and tasks are ready:
   ```
   get_project_details(project_id="PROJ-XXXX", detail_level="status")
   ```

### Step 1 — Read the Execution Manifest

Read the minimal MD file to get the project ID, task IDs, and batch structure.

If the manifest doesn't specify batches, derive them:
```
query_tasks(project_id="PROJ-XXXX", status="Approved", output_format="json")
query_relationships(project_id="PROJ-XXXX", relationship_type="depends")
```

Group by dependency depth:
- **Batch 1:** Tasks with no `depends` relationships
- **Batch 2:** Tasks whose dependencies are all in Batch 1
- **Batch N:** Tasks whose dependencies are all in Batches 1..N-1

### Step 2 — Dispatch Subagents

For each task in the current batch, dispatch a subagent with a **short prompt**:

```
Implement TASK-XXXX in project PROJ-XXXX.

Worktree: /path/to/worktree

Environment: Ensure the project's package manager CLI is on PATH before running commands.

Steps:
1. Call get_task_details(task_id="TASK-XXXX", sections=["planning","requirements","adrs"])
   to pull the full task specification, linked requirements, and architecture decisions.
2. Invoke superpowers:test-driven-development — write tests first.
3. Implement the task per the technical_outline and acceptance_criteria from the MCP.
4. Only modify files listed in files_touched.
5. Invoke simplify — review for redundancy and quality.
6. Invoke superpowers:verification-before-completion — run the verification_commands from the task.
7. Call update_task_status(task_id="TASK-XXXX", new_status="Implemented",
     execution_notes="<files created, symbols exposed, test results>",
     deviation_from_plan="<any deviations or 'none'>")
8. For each requirement linked to this task, call get_requirement_details(requirement_id="REQ-XXXX")
   and record its current status.
9. Return a brief summary: pass/fail, files modified, key symbols exposed,
   and the status of each linked requirement (e.g. "REQ-0001: Partially Implemented Validated").
```

**Why this works:**
- The subagent gets complete context via one MCP call — technical outline, acceptance criteria, scope boundaries, files to touch, verification commands, linked requirements, relevant ADRs.
- The main agent stays lean — no spec inlining, no prompt assembly, no context bloat.
- Each subagent is self-contained — it knows what to build, how to verify, and how to report status.

**Rules:**
- Fire parallel subagents for independent tasks in the same batch via `superpowers:dispatching-parallel-agents`.
- Keep the subagent prompt under 20 lines. All detail comes from the MCP, not the prompt.
- Include the worktree path so the subagent works in the right directory.
- **Worktree concurrency limit:** Creating 2+ worktrees simultaneously can cause `.git/config` lock contention (`error: could not lock config file .git/config: File exists`). **Create worktrees sequentially** (one at a time), then dispatch agents into the pre-created worktrees in parallel. Alternatively, dispatch agents one at a time with a brief delay between launches.
- **Requirement state race conditions:** When parallel agents update tasks linked to the same requirement, each agent may observe different intermediate requirement states due to auto-progression race conditions. This is expected — the final state converges correctly. Do not retry based on inconsistent intermediate states.

### Step 2.5 — Consolidate Worktree Changes

After subagents complete, their changes may be uncommitted in separate worktrees. Before validating:

1. For each worktree with changes, copy modified files to the main worktree:
   ```bash
   cp <worktree>/<file> <main>/<file>
   ```
2. Run the full test suite in the main worktree to verify all changes integrate correctly.
3. Only proceed to Step 3 (validate batch completion) after the integrated test suite passes.
4. Clean up worktrees: `git worktree remove <path> --force`

This step catches integration issues between parallel subagents that each passed their own tests in isolation.

### Step 3 — Validate Batch Completion

After all subagents in a batch return:

1. Check project status:
   ```
   get_project_details(project_id="PROJ-XXXX", detail_level="status")
   ```
2. Verify batch tasks moved to "Implemented":
   ```
   query_tasks(project_id="PROJ-XXXX", status="Implemented", output_format="summary")
   ```
3. If any task is still "Approved" (subagent failed), handle via Error Recovery below.

### Step 4 — Validate and Advance

After confirming implementation, the main agent validates the task:

```
update_task_status(
  task_id="TASK-XXXX",
  new_status="Validated",
  execution_notes="Verification complete: all acceptance criteria met."
)
```

**Validate tasks in dependency order** (leaf tasks first, then their dependents) to ensure requirement auto-progression happens correctly. If you validate tasks out of order, requirements may skip intermediate states.

**Auto-progression:** When a task is validated, the MCP automatically advances linked requirements:
- Some tasks validated → requirement moves to "Partially Implemented Validated"
- All tasks validated → requirement moves to "Validated"

**After validating each task, check whether any linked requirement reached "Validated".** The implementation subagent already reports linked requirement statuses in its return summary — use that to know which requirements to check. Confirm with:

```
get_requirement_details(requirement_id="REQ-XXXX")
```

If a requirement has auto-progressed to **"Validated"**, the main agent must immediately dispatch a **requirement validation subagent**:

```
Validate requirement REQ-XXXX in project PROJ-XXXX.

Steps:
1. Call get_requirement_details(requirement_id="REQ-XXXX", trace=true)
   to pull acceptance criteria, linked tasks, and linked ADRs.
2. For each acceptance criterion, verify it is satisfied by the implemented code.
   Read the relevant source files in the worktree to confirm.
3. Check architectural consistency:
   - The implementation respects layer boundaries described in docs/ARCHITECTURE.md.
   - No files outside files_touched on linked tasks were modified unexpectedly.
   - No VALID_RELATIONSHIP_COMBINATIONS or architectural constraints are violated.
4. If all acceptance criteria are met and no architectural violations found:
   Return PASS with a brief summary per criterion.
5. If any criterion is unmet or an architectural violation is found:
   Return FAIL with the specific criterion or violation and evidence.
   Do NOT update any MCP status on failure — return to main agent for decision.
```

This subagent does **not** write code — it reads and verifies only. It is the gate that confirms a requirement is truly done, not just that its tasks passed their individual verification commands.

**On PASS:** No MCP action needed — the requirement is already "Validated" (auto-progressed by MCP).

**On FAIL:** The requirement status must be corrected. The main agent decides:
- If the gap is in an existing task: re-open the relevant task and re-dispatch an implementation subagent.
- If the gap requires a new task: create it in the MCP and add it to the execution plan.

### Step 5 — Next Batch

Repeat Steps 2-4 for the next batch. Continue until all batches complete.

Between batches, the main agent may optionally query completed task execution notes to pass exposed symbols to the next batch's subagent prompts:
```
get_task_details(task_id="TASK-XXXX", sections=["execution"])
```

But this is often unnecessary — the subagent can discover available symbols by reading the codebase in the worktree.

---

## Error Recovery Protocol

### When a subagent fails

1. Read the failure summary from the subagent's return.
2. Re-dispatch with a correction note:
   ```
   Implement TASK-XXXX in project PROJ-XXXX.
   Worktree: /path/to/worktree

   Previous attempt failed: <brief error description>.
   Pull context from MCP, fix the specific issue, and complete implementation.
   ```
3. Maximum 2 retries. After 2 failures, leave the task in "Approved" status and stop — do not proceed to dependent batches.

### When a subagent deviates

- **Minor (acceptance criteria still met):** Accept. The subagent records it in `deviation_from_plan`.
- **Affects downstream tasks:** Note in the next batch's subagent prompt.
- **Conflicts with architecture:** Reject. Re-dispatch with the constraint called out.

### When `simplify` escalates

If a structural issue exceeds the task scope:
1. Can it be fixed with a targeted correction? Re-dispatch.
2. Requires architecture changes? Stop and escalate to planning.
3. Record in `deviation_from_plan` either way.

---

## Progress Monitoring

### During Execution

```
get_project_details(project_id="PROJ-XXXX", detail_level="status")
```

Returns requirement/task/ADR counts by status with completion percentage.

**Note:** `progress_pct` counts Implemented + Validated tasks; `validated_pct` counts only Validated tasks. During execution, `progress_pct` will climb as tasks are implemented, while `validated_pct` stays at 0% until Step 4 validation begins.

### After Execution

```
validate_project_plan(project_id="PROJ-XXXX", summary_only=false)
```

Confirms: all tasks validated, all requirements auto-progressed, no orphans.

### Change History

```
diff_project(project_id="PROJ-XXXX", from_timestamp="...", to_timestamp="...")
```

---

## Key Behavioral Notes

1. **Task approval requires approved requirements.** Tasks linked via `implements` cannot move to "Approved" unless the requirement is already "Approved". Plan the approval sequence in planning, not execution.

2. **Requirements auto-progress — and trigger a validation gate.** When tasks are validated, linked requirements automatically advance. If a requirement reaches "Validated", the main agent must dispatch a requirement validation subagent to verify all acceptance criteria and check architectural consistency. The implementation subagent reports linked requirement statuses in its return summary to enable this. Do not skip the gate — task-level verification commands are not the same as requirement-level acceptance criteria checks.

3. **`update_task_status` is narrow-write.** Accepts only `new_status`, `execution_notes`, `deviation_from_plan`. Use `update_task` for planning field changes (assignee, files_touched, effort).

4. **Subagents pull their own context.** The `get_task_details` call with `sections=["planning","requirements","adrs"]` returns everything a subagent needs: technical outline, acceptance criteria, scope boundaries, files to touch, verification commands, linked requirement criteria, and relevant ADR decisions — all in one call.

5. **Deviation tracking is built in.** The `deviation_from_plan` field on `update_task_status` captures departures structurally. No Handoff Block format needed.

6. **Export files are server-side.** `export_project_documentation` and `create_architectural_diagrams` write to the MCP server's filesystem, not the client's.

---

## MCP Tool Reference for Execution

### Subagent Tools (used by each subagent)
| Goal | Tool |
|------|------|
| Pull full task context | `get_task_details(task_id, sections=["planning","requirements","adrs"])` |
| Mark task implemented | `update_task_status(task_id, new_status="Implemented", execution_notes, deviation_from_plan)` |
| Check valid transitions | `get_valid_status_transitions(entity_type="task", current_status)` |

### Main Agent Tools (orchestration)
| Goal | Tool |
|------|------|
| Check project status | `get_project_details(project_id, detail_level="status")` |
| List tasks by status | `query_tasks(project_id, status, output_format="json")` |
| Get dependency chain | `query_relationships(project_id, relationship_type="depends")` |
| Validate task after implementation | `update_task_status(task_id, new_status="Validated")` |
| Check execution notes | `get_task_details(task_id, sections=["execution"])` |
| Full health check | `validate_project_plan(project_id, summary_only=true)` |
| Assign task to agent | `update_task(task_id, assignee="agent-1")` |

---

## Definition of Execution Complete

Execution is complete when:

1. All tasks are in "Validated" status
2. All linked requirements have auto-progressed to "Validated" (or "Partially Implemented Validated" for partial phases)
3. `validate_project_plan` returns 0 errors
4. `get_project_details(detail_level="status")` confirms expected completion percentage
5. All verification commands have passing output
6. No unresolved deviations conflict with architecture
7. Worktree changes are merged or PR'd to the main branch
