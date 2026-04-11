"""Shared constants for lifecycle-mcp handlers."""

ENTITY_TABLE_MAP: dict[str, str] = {
    "project": "projects",
    "requirement": "requirements",
    "task": "tasks",
    "architecture": "architecture",
}

REQUIREMENT_TRANSITIONS: dict[str, list[str]] = {
    "Under Review": ["Approved", "Deprecated"],
    "Approved": ["Deprecated"],
    "Partially Implemented": ["Deprecated"],
    "Partially Implemented Validated": ["Deprecated"],
    "Implemented": ["Deprecated"],
    "Partially Validated": ["Deprecated"],
    "Validated": ["Deprecated"],
    "Deprecated": [],
}
REQUIREMENT_STATUSES: set[str] = set(REQUIREMENT_TRANSITIONS.keys())

TASK_TRANSITIONS: dict[str, list[str]] = {
    "Under Review": ["Approved", "Deprecated"],
    "Approved": ["Implemented", "Deprecated"],
    "Implemented": ["Validated", "Deprecated"],
    "Validated": ["Deprecated"],
    "Deprecated": [],
}
TASK_STATUSES: set[str] = set(TASK_TRANSITIONS.keys())

ARCHITECTURE_TRANSITIONS: dict[str, list[str]] = {
    "Draft": ["Under Review", "Accepted", "Deprecated"],
    "Under Review": ["Proposed", "Approved", "Accepted", "Deprecated"],
    "Proposed": ["Accepted", "Rejected", "Deprecated"],
    "Accepted": ["Implemented", "Deprecated"],
    "Rejected": ["Deprecated"],
    "Deprecated": [],
    "Approved": ["Implemented", "Deprecated"],
    "Implemented": ["Deprecated"],
}
ARCHITECTURE_STATUSES: set[str] = set(ARCHITECTURE_TRANSITIONS.keys())

STATE_MACHINES: dict[str, dict[str, list[str]]] = {
    "requirement": REQUIREMENT_TRANSITIONS,
    "task": TASK_TRANSITIONS,
    "architecture": ARCHITECTURE_TRANSITIONS,
}

VALID_RELATIONSHIP_COMBINATIONS: set[tuple[str, str, str]] = {
    ("requirement", "task", "implements"),
    ("task", "requirement", "implements"),
    ("task", "requirement", "addresses"),
    ("requirement", "architecture", "addresses"),
    ("architecture", "requirement", "addresses"),
    ("task", "architecture", "implements"),
    ("task", "architecture", "informs"),
    ("architecture", "task", "informs"),
    ("task", "task", "depends"),
    ("task", "task", "blocks"),
    ("task", "task", "informs"),
    ("task", "task", "requires"),
    ("requirement", "requirement", "depends"),
    ("requirement", "requirement", "parent"),
    ("requirement", "requirement", "refines"),
    ("requirement", "requirement", "conflicts"),
    ("requirement", "requirement", "relates"),
}
