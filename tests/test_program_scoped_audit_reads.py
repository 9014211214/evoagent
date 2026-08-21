"""Regression controls isolate lifecycle audit reads by Program identity."""

from types import SimpleNamespace

from evoagent.program import EvolutionProgramController
from evoagent.program.controller_program_scope_final import (
    RetryHardenedEvolutionProgramController,
    _AuditBoundController,
    _ProgramScopedRepository,
)


class _Repository:
    def __init__(self, events):
        self._events = tuple(events)

    def events(self):
        return self._events


class _ControllerHarness(RetryHardenedEvolutionProgramController):
    def __init__(self, repository):
        self.repository = repository

    def _campaign_evidence(self, campaign):
        return (
            None,
            None,
            None,
            SimpleNamespace(program_id=campaign.program_id),
        )


def test_program_scoped_repository_filters_same_generation_from_other_programs():
    repository = _Repository(
        (
            SimpleNamespace(program_id="program:a", generation_id="g1"),
            SimpleNamespace(program_id="program:b", generation_id="g1"),
            SimpleNamespace(program_id="program:a", generation_id="g2"),
        )
    )

    scoped = _ProgramScopedRepository(repository, "program:a")

    assert tuple(
        (event.program_id, event.generation_id)
        for event in scoped.events()
    ) == (
        ("program:a", "g1"),
        ("program:a", "g2"),
    )


def test_final_controller_delegates_audit_validation_with_scoped_events(monkeypatch):
    repository = _Repository(
        (
            SimpleNamespace(program_id="program:a", generation_id="g1"),
            SimpleNamespace(program_id="program:b", generation_id="g1"),
        )
    )
    controller = _ControllerHarness(repository)
    observed = []

    def probe(self, campaign, approvals):
        observed.extend(event.program_id for event in self.repository.events())

    monkeypatch.setattr(
        _AuditBoundController,
        "_validate_campaign_lifecycle_audit",
        probe,
    )

    controller._validate_campaign_lifecycle_audit(
        SimpleNamespace(program_id="program:a"),
        (),
    )

    assert observed == ["program:a"]
    assert controller.repository is repository


def test_public_controller_exposes_program_scoped_final_layer():
    assert issubclass(
        EvolutionProgramController,
        RetryHardenedEvolutionProgramController,
    )
    assert EvolutionProgramController.__module__ == (
        "evoagent.program.controller_program_attestation_final"
    )
