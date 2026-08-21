from evoagent.composite import (
    CompositeComponentDriftError,
    CompositeEvaluationRoleError,
    CompositeEvaluationService,
    CompositeSnapshotEvaluation,
    CompositeSnapshotManifest,
    CompositeSnapshotService,
    CompositeStopDecision,
    SQLiteCompositeEvaluationRepository,
    SQLiteCompositeSnapshotRegistry,
    StaleCompositeRevision,
    build_composite_snapshot_manifest,
    build_composite_stop_decision,
)


def test_composite_public_api_exposes_governed_pointer_contract():
    assert SQLiteCompositeSnapshotRegistry.__module__ == (
        "evoagent.composite.repository"
    )
    assert CompositeSnapshotService.__module__ == (
        "evoagent.composite.service"
    )
    assert CompositeSnapshotManifest.__module__ == (
        "evoagent.composite.models"
    )
    assert CompositeComponentDriftError.__module__ == (
        "evoagent.composite.service"
    )
    assert StaleCompositeRevision.__module__ == (
        "evoagent.composite.repository"
    )
    assert build_composite_snapshot_manifest.__module__ == (
        "evoagent.composite.builders"
    )


def test_composite_public_api_exposes_frozen_evaluation_contract():
    assert SQLiteCompositeEvaluationRepository.__module__ == (
        "evoagent.composite.evaluation_repository"
    )
    assert CompositeEvaluationService.__module__ == (
        "evoagent.composite.evaluation_service"
    )
    assert CompositeSnapshotEvaluation.__module__ == (
        "evoagent.composite.evaluation"
    )
    assert CompositeStopDecision.__module__ == (
        "evoagent.composite.evaluation"
    )
    assert CompositeEvaluationRoleError.__module__ == (
        "evoagent.composite.evaluation_service"
    )
    assert build_composite_stop_decision.__module__ == (
        "evoagent.composite.evaluation"
    )
