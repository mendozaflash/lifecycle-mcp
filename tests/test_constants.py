"""
Tests for the shared constants module.

Verifies that all state machines, relationship rules, and entity table maps
are correctly defined and consistent.
"""

from lifecycle_mcp.constants import (
    ARCHITECTURE_STATUSES,
    ARCHITECTURE_TRANSITIONS,
    ENTITY_TABLE_MAP,
    REQUIREMENT_STATUSES,
    REQUIREMENT_TRANSITIONS,
    STATE_MACHINES,
    TASK_STATUSES,
    TASK_TRANSITIONS,
    VALID_RELATIONSHIP_COMBINATIONS,
)


# ---------------------------------------------------------------
# State machine dict existence and key coverage
# ---------------------------------------------------------------


class TestStateMachineStructure:
    """Verify all three state machine dicts exist and have expected keys."""

    def test_requirement_transitions_keys(self):
        expected = {
            "Draft", "Under Review", "Approved", "Architecture",
            "Ready", "Implemented", "Validated", "Deprecated",
        }
        assert set(REQUIREMENT_TRANSITIONS.keys()) == expected

    def test_task_transitions_keys(self):
        expected = {"Not Started", "In Progress", "Blocked", "Complete", "Abandoned"}
        assert set(TASK_TRANSITIONS.keys()) == expected

    def test_architecture_transitions_keys(self):
        expected = {
            "Draft", "Under Review", "Proposed", "Accepted",
            "Rejected", "Deprecated", "Approved", "Implemented",
        }
        assert set(ARCHITECTURE_TRANSITIONS.keys()) == expected


# ---------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------


class TestTerminalStates:
    """Deprecated is terminal for requirements and architecture;
    Complete and Abandoned are terminal for tasks."""

    def test_requirement_deprecated_is_terminal(self):
        assert REQUIREMENT_TRANSITIONS["Deprecated"] == []

    def test_architecture_deprecated_is_terminal(self):
        assert ARCHITECTURE_TRANSITIONS["Deprecated"] == []

    def test_task_complete_is_terminal(self):
        assert TASK_TRANSITIONS["Complete"] == []

    def test_task_abandoned_is_terminal(self):
        assert TASK_TRANSITIONS["Abandoned"] == []


# ---------------------------------------------------------------
# Status sets derived from transition dicts
# ---------------------------------------------------------------


class TestStatusSets:
    """TASK_STATUSES, REQUIREMENT_STATUSES, ARCHITECTURE_STATUSES
    are derived from their respective transition dict keys."""

    def test_task_statuses_equals_keys(self):
        assert TASK_STATUSES == set(TASK_TRANSITIONS.keys())

    def test_task_statuses_has_all_five(self):
        assert TASK_STATUSES == {
            "Not Started", "In Progress", "Blocked", "Complete", "Abandoned"
        }

    def test_requirement_statuses_equals_keys(self):
        assert REQUIREMENT_STATUSES == set(REQUIREMENT_TRANSITIONS.keys())

    def test_architecture_statuses_equals_keys(self):
        assert ARCHITECTURE_STATUSES == set(ARCHITECTURE_TRANSITIONS.keys())


# ---------------------------------------------------------------
# VALID_RELATIONSHIP_COMBINATIONS
# ---------------------------------------------------------------


class TestRelationshipCombinations:
    """VALID_RELATIONSHIP_COMBINATIONS is a set containing all expected tuples."""

    def test_is_a_set(self):
        assert isinstance(VALID_RELATIONSHIP_COMBINATIONS, set)

    def test_contains_task_implements_requirement(self):
        assert ("task", "requirement", "implements") in VALID_RELATIONSHIP_COMBINATIONS

    def test_contains_requirement_implements_task(self):
        assert ("requirement", "task", "implements") in VALID_RELATIONSHIP_COMBINATIONS

    def test_contains_task_architecture_implements(self):
        assert ("task", "architecture", "implements") in VALID_RELATIONSHIP_COMBINATIONS

    def test_contains_task_task_depends(self):
        assert ("task", "task", "depends") in VALID_RELATIONSHIP_COMBINATIONS

    def test_contains_requirement_requirement_parent(self):
        assert ("requirement", "requirement", "parent") in VALID_RELATIONSHIP_COMBINATIONS

    def test_expected_count(self):
        # 17 entries per the spec
        assert len(VALID_RELATIONSHIP_COMBINATIONS) == 17


# ---------------------------------------------------------------
# ENTITY_TABLE_MAP
# ---------------------------------------------------------------


class TestEntityTableMap:
    """ENTITY_TABLE_MAP contains all 4 entity types."""

    def test_has_four_entries(self):
        assert len(ENTITY_TABLE_MAP) == 4

    def test_project_maps_to_projects(self):
        assert ENTITY_TABLE_MAP["project"] == "projects"

    def test_requirement_maps_to_requirements(self):
        assert ENTITY_TABLE_MAP["requirement"] == "requirements"

    def test_task_maps_to_tasks(self):
        assert ENTITY_TABLE_MAP["task"] == "tasks"

    def test_architecture_maps_to_architecture(self):
        assert ENTITY_TABLE_MAP["architecture"] == "architecture"


# ---------------------------------------------------------------
# STATE_MACHINES aggregate dict
# ---------------------------------------------------------------


class TestStateMachinesAggregate:
    """STATE_MACHINES aggregates all three transition dicts."""

    def test_has_three_keys(self):
        assert set(STATE_MACHINES.keys()) == {"requirement", "task", "architecture"}

    def test_requirement_entry_is_same_object(self):
        assert STATE_MACHINES["requirement"] is REQUIREMENT_TRANSITIONS

    def test_task_entry_is_same_object(self):
        assert STATE_MACHINES["task"] is TASK_TRANSITIONS

    def test_architecture_entry_is_same_object(self):
        assert STATE_MACHINES["architecture"] is ARCHITECTURE_TRANSITIONS


# ---------------------------------------------------------------
# Shortcut: Architecture Draft -> Accepted
# ---------------------------------------------------------------


class TestArchitectureShortcut:
    """ARCHITECTURE_TRANSITIONS['Draft'] includes 'Accepted' as a shortcut."""

    def test_draft_includes_accepted(self):
        assert "Accepted" in ARCHITECTURE_TRANSITIONS["Draft"]
