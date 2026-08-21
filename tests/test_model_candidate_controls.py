from __future__ import annotations

from datetime import datetime, timezone

import pytest

from evoagent.benchmarks.models import ResourceBudget
from evoagent.lab import GovernedModelEvolutionLab
from evoagent.model_registry import (
    AllowlistedTrainingAuthorizationVerifier,
    ExternalModelCandidateManifest,
    ExternalTrainingReceipt,
    IndependentModelCandidateEvaluator,
    ModelActivationPolicy,
    ModelActivationThresholds,
    ModelCandidateAdmissionService,
    ModelCandidateValidationError,
    ModelEvaluationError,
    ModelVersionStatus,
    SQLiteModelRegistry,
    SyntheticCandidateProfile,
    SyntheticModelCandidateAdapter,
    TrainingReceiptKind,
    build_external_candidate_manifest,
    build_external_training_receipt,
    build_initial_model_manifest,
    build_model_evaluation_suite,
    build_training_authorization_reference,
    canonical_sha256,
)
from evoagent.training import (
    ModelEvolutionPackageManager,
    TrainingBudget,
    TrainingMethod,
)


INITIAL_CREATED_AT = datetime(
    2026,
    8,
    10,
    14,
    0,
    tzinfo=timezone.utc,
)
RECEIPT_STARTED_AT = datetime(
    2026,
    8,
    10,
    15,
    10,
    tzinfo=timezone.utc,
)
RECEIPT_COMPLETED_AT = datetime(
    2026,
    8,
    10,
    15,
    20,
    tzinfo=timezone.utc,
)
CANDIDATE_CREATED_AT = datetime(
    2026,
    8,
    10,
    15,
    21,
    tzinfo=timezone.utc,
)
DECIDED_AT = datetime(
    2026,
    8,
    10,
    15,
    30,
    tzinfo=timezone.utc,
)
FAMILY_ID = "candidate-control-family-v1"
TRAINER_ID = "external-control-trainer"


@pytest.fixture(scope="module")
def training_package(tmp_path_factory):
    root = tmp_path_factory.mktemp("model-candidate-controls")
    result = GovernedModelEvolutionLab(
        root / "training-intent",
        source_commit="d" * 40,
    ).run()
    return ModelEvolutionPackageManager().load_file(result.package_path)


def _artifacts(
    package,
    *,
    profile: SyntheticCandidateProfile = SyntheticCandidateProfile.PASSING,
    candidate_id: str | None = None,
):
    candidate_id = candidate_id or f"synthetic/control-{profile.value}-v1"
    authorization = build_training_authorization_reference(
        reference_id=f"authorization-{profile.value}-v1",
        signer_identity="external-control-authority",
        external_verification_uri=(
            f"synthetic://authorization/{profile.value}-v1"
        ),
        authorization_payload={
            "candidate_id": candidate_id,
            "training_intent_package_hash": package.package_hash,
            "synthetic_fixture": True,
        },
    )
    candidate = build_external_candidate_manifest(
        package,
        family_id=FAMILY_ID,
        candidate_id=candidate_id,
        version="1.1.0",
        authorization=authorization,
        generated_by=TRAINER_ID,
        training_commit="6" * 40,
        created_at=CANDIDATE_CREATED_AT,
        synthetic_profile=profile,
    )
    receipt = build_external_training_receipt(
        package,
        candidate,
        receipt_id=f"receipt-{profile.value}-v1",
        trainer_id=TRAINER_ID,
        started_at=RECEIPT_STARTED_AT,
        completed_at=RECEIPT_COMPLETED_AT,
        budget_used=TrainingBudget(
            max_gpu_hours=0.0,
            max_rollouts=32,
            max_training_tokens=0,
            max_cost_usd=0.0,
        ),
        receipt_kind=TrainingReceiptKind.SYNTHETIC_LIFECYCLE_FIXTURE,
    )
    return authorization, candidate, receipt


def _registry_with_base(tmp_path, package):
    registry = SQLiteModelRegistry(tmp_path / "models.db")
    initial = build_initial_model_manifest(
        package,
        family_id=FAMILY_ID,
        version="1.0.0",
        created_at=INITIAL_CREATED_AT,
    )
    registry.register_initial(
        initial,
        reason="Register synthetic candidate-control base model.",
        actor_id="candidate-control-bootstrap",
    )
    return registry, initial


def _rehash_candidate(candidate, **updates):
    payload = candidate.model_dump(
        mode="python",
        exclude={"manifest_hash"},
    )
    payload.update(updates)
    return ExternalModelCandidateManifest(
        **payload,
        manifest_hash=canonical_sha256(payload),
    )


def _rehash_receipt(receipt, **updates):
    payload = receipt.model_dump(
        mode="python",
        exclude={"receipt_hash"},
    )
    payload.update(updates)
    return ExternalTrainingReceipt(
        **payload,
        receipt_hash=canonical_sha256(payload),
    )


def test_admission_rejects_unverified_authorization_by_default(
    tmp_path,
    training_package,
):
    _, candidate, receipt = _artifacts(training_package)
    registry, initial = _registry_with_base(tmp_path, training_package)
    service = ModelCandidateAdmissionService(
        registry=registry,
        allow_synthetic_fixture=True,
    )

    with pytest.raises(
        ModelCandidateValidationError,
        match="not externally allowlisted",
    ):
        service.admit(
            package=training_package,
            candidate=candidate,
            receipt=receipt,
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert len(registry.list_versions(FAMILY_ID)) == 1


def test_admission_rejects_synthetic_fixture_unless_explicitly_enabled(
    tmp_path,
    training_package,
):
    authorization, candidate, receipt = _artifacts(training_package)
    registry, initial = _registry_with_base(tmp_path, training_package)
    service = ModelCandidateAdmissionService(
        registry=registry,
        authorization_verifier=AllowlistedTrainingAuthorizationVerifier(
            {authorization.reference_hash}
        ),
    )

    with pytest.raises(
        ModelCandidateValidationError,
        match="Synthetic training receipts are disabled",
    ):
        service.admit(
            package=training_package,
            candidate=candidate,
            receipt=receipt,
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert len(registry.list_versions(FAMILY_ID)) == 1


@pytest.mark.parametrize(
    "drift",
    (
        "base_model",
        "method",
        "evidence",
        "held_out",
        "artifact_hash",
        "source_commit",
        "budget",
        "authorization",
    ),
)
def test_admission_rejects_every_governed_binding_drift(
    tmp_path,
    training_package,
    drift,
):
    authorization, candidate, receipt = _artifacts(
        training_package,
        candidate_id=f"synthetic/control-drift-{drift}-v1",
    )
    verifier_hash = authorization.reference_hash

    if drift == "base_model":
        candidate = _rehash_candidate(
            candidate,
            base_model_id="synthetic/other-base-model",
        )
        receipt = _rehash_receipt(
            receipt,
            base_model_id="synthetic/other-base-model",
        )
    elif drift == "method":
        candidate = _rehash_candidate(
            candidate,
            training_method=TrainingMethod.SFT,
        )
        receipt = _rehash_receipt(
            receipt,
            training_method=TrainingMethod.SFT,
        )
    elif drift == "evidence":
        candidate = _rehash_candidate(
            candidate,
            evidence_manifest_hash="e" * 64,
        )
        receipt = _rehash_receipt(
            receipt,
            evidence_manifest_hash="e" * 64,
        )
    elif drift == "held_out":
        candidate = _rehash_candidate(
            candidate,
            held_out_task_ids=("different-held-out-task",),
        )
        receipt = _rehash_receipt(
            receipt,
            held_out_task_ids=("different-held-out-task",),
        )
    elif drift == "artifact_hash":
        receipt = _rehash_receipt(
            receipt,
            artifact_sha256="f" * 64,
        )
    elif drift == "source_commit":
        candidate = _rehash_candidate(
            candidate,
            source_commit="e" * 40,
        )
    elif drift == "budget":
        receipt = _rehash_receipt(
            receipt,
            budget_used=TrainingBudget(
                max_gpu_hours=0.0,
                max_rollouts=65,
                max_training_tokens=0,
                max_cost_usd=0.0,
            ),
        )
    elif drift == "authorization":
        other_authorization = build_training_authorization_reference(
            reference_id="other-authorization-v1",
            signer_identity="other-control-authority",
            external_verification_uri=(
                "synthetic://authorization/other-v1"
            ),
            authorization_payload={"different": True},
        )
        candidate = _rehash_candidate(
            candidate,
            training_authorization=other_authorization,
        )
        receipt = _rehash_receipt(
            receipt,
            authorization_reference_hash=(
                other_authorization.reference_hash
            ),
        )

    registry, initial = _registry_with_base(tmp_path, training_package)
    service = ModelCandidateAdmissionService(
        registry=registry,
        authorization_verifier=AllowlistedTrainingAuthorizationVerifier(
            {verifier_hash}
        ),
        allow_synthetic_fixture=True,
    )

    with pytest.raises(ModelCandidateValidationError):
        service.admit(
            package=training_package,
            candidate=candidate,
            receipt=receipt,
        )

    assert registry.active(FAMILY_ID).model_id == initial.model_id
    assert len(registry.list_versions(FAMILY_ID)) == 1


def test_candidate_manifest_rejects_unsafe_or_local_artifact_uri(
    training_package,
):
    _, candidate, _ = _artifacts(training_package)
    payload = candidate.model_dump(
        mode="python",
        exclude={"manifest_hash"},
    )
    payload["artifact_uri"] = "file:///tmp/candidate.safetensors"

    with pytest.raises(ValueError, match="must use https, s3, gs, hf, or synthetic"):
        ExternalModelCandidateManifest(
            **payload,
            manifest_hash=canonical_sha256(payload),
        )


@pytest.mark.parametrize(
    "profile,expected_activate",
    (
        (SyntheticCandidateProfile.PASSING, True),
        (SyntheticCandidateProfile.REGRESSING, False),
        (SyntheticCandidateProfile.UNSAFE, False),
        (SyntheticCandidateProfile.OVER_BUDGET, False),
    ),
)
def test_independent_evaluation_profiles_gate_activation(
    tmp_path,
    training_package,
    profile,
    expected_activate,
):
    _, candidate, _ = _artifacts(
        training_package,
        profile=profile,
        candidate_id=f"synthetic/evaluation-{profile.value}-v1",
    )
    suite = build_model_evaluation_suite(
        training_package,
        suite_id=f"evaluation-suite-{profile.value}-v1",
    )
    budget = ResourceBudget(
        max_task_trials=6,
        max_tokens=0,
        max_tool_calls=(10 if profile == SyntheticCandidateProfile.OVER_BUDGET else 50),
        max_wall_seconds=0.0,
        max_cost_usd=0.0,
    )
    evaluator = IndependentModelCandidateEvaluator(
        tmp_path / profile.value
    )
    report = evaluator.evaluate(
        candidate=candidate,
        adapter=SyntheticModelCandidateAdapter(candidate),
        suite=suite,
        evaluator_id="independent-control-evaluator",
        trainer_id=TRAINER_ID,
        budget=budget,
    )
    decision = ModelActivationPolicy().decide(
        report,
        thresholds=ModelActivationThresholds(),
        decided_by="independent-control-policy",
        decided_at=DECIDED_AT,
    )

    assert decision.activate is expected_activate
    if profile == SyntheticCandidateProfile.PASSING:
        assert report.held_out_base_score == 0.0
        assert report.held_out_candidate_score == 1.0
        assert report.replay_candidate_score == 1.0
        assert report.retention_candidate_score == 1.0
        assert report.safety_candidate_score == 1.0
        assert report.regression_count == 0
        assert report.safety_violation_count == 0
        assert report.candidate_budget_ok is True
    elif profile == SyntheticCandidateProfile.REGRESSING:
        assert report.retention_candidate_score == 0.0
        assert report.regression_count == 1
        assert report.forgetting_rate == 1.0
    elif profile == SyntheticCandidateProfile.UNSAFE:
        assert report.safety_candidate_score == 0.0
        assert report.safety_violation_count >= 1
    else:
        assert report.candidate_budget_ok is False
        assert report.candidate_usage.tool_calls > budget.max_tool_calls


def test_independent_evaluator_rejects_trainer_identity(
    tmp_path,
    training_package,
):
    _, candidate, _ = _artifacts(training_package)
    suite = build_model_evaluation_suite(training_package)

    with pytest.raises(ModelEvaluationError, match="identities to differ"):
        IndependentModelCandidateEvaluator(tmp_path / "evaluation").evaluate(
            candidate=candidate,
            adapter=SyntheticModelCandidateAdapter(candidate),
            suite=suite,
            evaluator_id=TRAINER_ID,
            trainer_id=TRAINER_ID,
            budget=ResourceBudget(
                max_task_trials=6,
                max_tokens=0,
                max_tool_calls=50,
                max_wall_seconds=0.0,
                max_cost_usd=0.0,
            ),
        )
