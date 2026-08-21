from datetime import timedelta

import pytest

from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.program import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManager,
)


def _package(tmp_path):
    result = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="4" * 40,
    ).run()
    return EvolutionProgramPackageManager().load_file(result.package_path)


def test_budget_control_rejects_feedback_producer_as_ingestor(tmp_path):
    package = _package(tmp_path)
    control = package.budget_control
    signal_event = control.events[2].model_copy(
        update={"actor_id": control.signals[0].evidence_producer_id}
    )
    events = (*control.events[:2], signal_event, *control.events[3:])
    forged = control.model_copy(update={"events": events})

    with pytest.raises(
        EvolutionProgramPackageError,
        match="feedback ingestor equals",
    ):
        EvolutionProgramPackageManager._verify_control_audit_semantics(
            forged
        )


def test_ambiguous_control_rejects_proxy_attribution_actor(tmp_path):
    package = _package(tmp_path)
    control = package.ambiguous_control
    attribution_event = control.events[3].model_copy(
        update={"actor_id": "proxy-attribution-writer"}
    )
    events = (*control.events[:3], attribution_event, *control.events[4:])
    forged = control.model_copy(update={"events": events})

    with pytest.raises(
        EvolutionProgramPackageError,
        match="Attribution actor differs",
    ):
        EvolutionProgramPackageManager._verify_control_audit_semantics(
            forged
        )


def test_control_rejects_decision_actor_role_overlap(tmp_path):
    package = _package(tmp_path)
    control = package.budget_control
    decision = control.decisions[0].model_copy(
        update={"decided_by": control.events[2].actor_id}
    )
    decision_event = control.events[3].model_copy(
        update={"actor_id": control.events[2].actor_id}
    )
    terminal_event = control.events[4].model_copy(
        update={"actor_id": control.events[2].actor_id}
    )
    forged = control.model_copy(
        update={
            "decisions": (decision,),
            "events": (
                *control.events[:3],
                decision_event,
                terminal_event,
            ),
        }
    )

    with pytest.raises(
        EvolutionProgramPackageError,
        match="decision actor violates role separation",
    ):
        EvolutionProgramPackageManager._verify_control_audit_semantics(
            forged
        )


def test_control_rejects_reason_and_time_rewrites(tmp_path):
    package = _package(tmp_path)
    control = package.ambiguous_control
    reason_event = control.events[-1].model_copy(
        update={"reason": "forged terminal rationale"}
    )
    reason_forged = control.model_copy(
        update={"events": (*control.events[:-1], reason_event)}
    )
    with pytest.raises(
        EvolutionProgramPackageError,
        match="audit reason differs",
    ):
        EvolutionProgramPackageManager._verify_control_audit_semantics(
            reason_forged
        )

    time_event = control.events[3].model_copy(
        update={
            "created_at": control.events[3].created_at + timedelta(seconds=1)
        }
    )
    time_forged = control.model_copy(
        update={
            "events": (
                *control.events[:3],
                time_event,
                *control.events[4:],
            )
        }
    )
    with pytest.raises(
        EvolutionProgramPackageError,
        match="event time differs",
    ):
        EvolutionProgramPackageManager._verify_control_audit_semantics(
            time_forged
        )


@pytest.mark.parametrize("field", ("generations", "signals", "decisions"))
def test_control_policy_rejects_missing_required_record_without_index_error(
    tmp_path,
    field,
):
    package = _package(tmp_path)
    control = package.budget_control.model_copy(update={field: ()})

    with pytest.raises(
        EvolutionProgramPackageError,
        match="requires one generation, signal and decision",
    ):
        EvolutionProgramPackageManager._verify_control_policy(control)
