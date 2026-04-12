"""
Tests for the shared constants module.

Verifies that all state machines, relationship rules, and entity table maps
are correctly defined and consistent.
"""

from lifecycle_mcp.constants import (
    ARCHITECTURE_STATUSES,
    ARCHITECTURE_TRANSITIONS,
    ENTITY_TABLE_MAP,
    PATTERN_TYPES,
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
            "Under Review", "Approved", "Partially Implemented",
            "Partially Implemented Validated", "Implemented",
            "Partially Validated", "Validated", "Deprecated",
        }
        assert set(REQUIREMENT_TRANSITIONS.keys()) == expected

    def test_task_transitions_keys(self):
        expected = {
            "Under Review", "Approved", "Implemented",
            "Validated", "Deprecated",
        }
        assert set(TASK_TRANSITIONS.keys()) == expected

    def test_architecture_transitions_keys(self):
        expected = {
            "Under Review", "Proposed", "Accepted",
            "Rejected", "Deprecated",
        }
        assert set(ARCHITECTURE_TRANSITIONS.keys()) == expected


# ---------------------------------------------------------------
# Terminal states
# ---------------------------------------------------------------


class TestTerminalStates:
    """Deprecated is the single terminal state for requirements, tasks,
    and architecture."""

    def test_requirement_deprecated_is_terminal(self):
        assert REQUIREMENT_TRANSITIONS["Deprecated"] == []

    def test_architecture_deprecated_is_terminal(self):
        assert ARCHITECTURE_TRANSITIONS["Deprecated"] == []

    def test_task_deprecated_is_terminal(self):
        assert TASK_TRANSITIONS["Deprecated"] == []


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
            "Under Review", "Approved", "Implemented",
            "Validated", "Deprecated",
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
        # 19 entries: 17 original + 2 architecture<->requirement informs (TASK-0026)
        assert len(VALID_RELATIONSHIP_COMBINATIONS) == 19


# ---------------------------------------------------------------
# ENTITY_TABLE_MAP
# ---------------------------------------------------------------


class TestEntityTableMap:
    """ENTITY_TABLE_MAP contains all 5 entity types."""

    def test_has_five_entries(self):
        assert len(ENTITY_TABLE_MAP) == 5

    def test_project_maps_to_projects(self):
        assert ENTITY_TABLE_MAP["project"] == "projects"

    def test_requirement_maps_to_requirements(self):
        assert ENTITY_TABLE_MAP["requirement"] == "requirements"

    def test_task_maps_to_tasks(self):
        assert ENTITY_TABLE_MAP["task"] == "tasks"

    def test_architecture_maps_to_architecture(self):
        assert ENTITY_TABLE_MAP["architecture"] == "architecture"

    def test_architectural_pattern_maps_to_architectural_patterns(self):
        assert ENTITY_TABLE_MAP["architectural_pattern"] == "architectural_patterns"


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
# Shortcut: Architecture Under Review -> Accepted
# ---------------------------------------------------------------


class TestArchitectureShortcut:
    """ARCHITECTURE_TRANSITIONS['Under Review'] includes 'Accepted' as a shortcut."""

    def test_under_review_includes_accepted(self):
        assert "Accepted" in ARCHITECTURE_TRANSITIONS["Under Review"]


# ---------------------------------------------------------------
# Requirement transition details (per acceptance criteria)
# ---------------------------------------------------------------


class TestRequirementTransitionDetails:
    """Verify exact allowed transitions for each requirement state.

    Only manual transitions are in the dict. Auto-only forward
    transitions (e.g. Approved -> Partially Implemented) are excluded
    because they are enforced by DB triggers, not app logic.
    """

    def test_under_review_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Under Review"] == ["Approved", "Deprecated"]

    def test_approved_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Approved"] == ["Deprecated"]

    def test_partially_implemented_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Partially Implemented"] == ["Deprecated"]

    def test_partially_implemented_validated_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Partially Implemented Validated"] == ["Deprecated"]

    def test_implemented_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Implemented"] == ["Deprecated"]

    def test_partially_validated_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Partially Validated"] == ["Deprecated"]

    def test_validated_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Validated"] == ["Deprecated"]

    def test_deprecated_transitions(self):
        assert REQUIREMENT_TRANSITIONS["Deprecated"] == []


# ---------------------------------------------------------------
# Task transition details (per acceptance criteria)
# ---------------------------------------------------------------


class TestTaskTransitionDetails:
    """Verify exact allowed transitions for each task state."""

    def test_under_review_transitions(self):
        assert TASK_TRANSITIONS["Under Review"] == ["Approved", "Deprecated"]

    def test_approved_transitions(self):
        assert TASK_TRANSITIONS["Approved"] == ["Implemented", "Deprecated"]

    def test_implemented_transitions(self):
        assert TASK_TRANSITIONS["Implemented"] == ["Validated", "Deprecated"]

    def test_validated_transitions(self):
        assert TASK_TRANSITIONS["Validated"] == ["Deprecated"]

    def test_deprecated_transitions(self):
        assert TASK_TRANSITIONS["Deprecated"] == []


# ---------------------------------------------------------------
# Architecture transition details (simplified state machine)
# ---------------------------------------------------------------


class TestArchitectureTransitionDetails:
    """Verify exact allowed transitions for each architecture state."""

    def test_architecture_has_exactly_5_states(self):
        assert len(ARCHITECTURE_TRANSITIONS) == 5

    def test_under_review_transitions(self):
        assert ARCHITECTURE_TRANSITIONS["Under Review"] == ["Proposed", "Accepted", "Deprecated"]

    def test_proposed_transitions(self):
        assert ARCHITECTURE_TRANSITIONS["Proposed"] == ["Accepted", "Rejected", "Deprecated"]

    def test_accepted_transitions(self):
        assert ARCHITECTURE_TRANSITIONS["Accepted"] == ["Deprecated"]

    def test_rejected_transitions(self):
        assert ARCHITECTURE_TRANSITIONS["Rejected"] == ["Deprecated"]

    def test_deprecated_transitions(self):
        assert ARCHITECTURE_TRANSITIONS["Deprecated"] == []


# ---------------------------------------------------------------
# PATTERN_TYPES
# ---------------------------------------------------------------


class TestPatternTypes:
    """PATTERN_TYPES is a frozenset of 15 valid pattern type values."""

    def test_is_frozenset(self):
        assert isinstance(PATTERN_TYPES, frozenset)

    def test_has_15_values(self):
        assert len(PATTERN_TYPES) == 15

    def test_contains_expected_types(self):
        expected = {
            "database", "api", "transport", "adapter", "auth", "schema", "messaging", "ui",
            "reliability", "modularity", "performance", "security",
            "scalability", "testability", "observability",
        }
        assert PATTERN_TYPES == expected
