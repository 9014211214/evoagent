from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.champion import ChampionDecisionPackageManager
from evoagent.lab.champion_promotion import BenchmarkGatedChampionLab
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.model_registry.models import canonical_sha256
from evoagent.release import (
    ReleaseAssessmentStatus,
    ReleaseDecisionAction,
    ReleaseEvidenceImporter,
    ReleaseEvidencePackageManager,
    ReleaseEvidenceSource,
    ReleaseLifecycleService,
    ReleaseStageKind,
    ReleaseState,
    SQLiteReleaseRegistry,
    build_release_plan,
    build_release_policy,
    build_release_segment,
    build_release_stage,
)


class ReleaseScenarioResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    scenario_id: str
    resumed: bool
    plan_id: str
    plan_hash: str
    actions: tuple[str, ...]
    assessment_statuses: tuple[str, ...]
    final_state: str
    final_primary_snapshot_id: str
    final_active_stage_id: str | None
    final_candidate_allocation_percent: float
    final_revision: int = Field(ge=0)
    release_campaign_id: str
    release_campaign_state: str
    release_approval_count: int = Field(ge=0)
    rollback_campaign_id: str | None = None
    rollback_campaign_state: str | None = None
    rollback_approval_count: int = Field(ge=0)
    rollback_reasons: tuple[str, ...] = ()
    batch_count: int = Field(ge=0)
    release_event_count: int = Field(ge=0)
    campaign_event_count: int = Field(ge=0)
    package_path: str
    package_hash: str


class ShadowCanaryReleaseLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    champion_package_hash: str
    incumbent_snapshot_id: str
    challenger_snapshot_id: str
    drift: ReleaseScenarioResult
    passing: ReleaseScenarioResult
    second_run_read_only: Literal[True] = True
    synthetic_fixture: Literal[True] = True
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    external_rollout_performed_by_evoagent: Literal[False] = False
    production_traffic_observed_by_evoagent: Literal[False] = False
    production_deployment_performed: Literal[False] = False
    external_rollback_performed: Literal[False] = False
    upload_performed: Literal[False] = False
    official_benchmark_claimed: Literal[False] = False


class ShadowCanaryReleaseLab:
    """Offline release evidence: one ready control and one governed rollback control."""

    RUN_ID = "shadow-canary-release-lab-v1"

    def __init__(
        self,
        root: str | Path,
        *,
        source_commit: str = "0" * 40,
        source_repository: str = (
            "https://github.com/9014211214/evoagent"
        ),
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Release lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef" for character in source_commit
        ):
            raise ValueError("source_commit must be lowercase 40-character Git hex.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository

    @property
    def champion_root(self) -> Path:
        return self.root / "champion"

    def run(self) -> ShadowCanaryReleaseLabResult:
        champion_lab = BenchmarkGatedChampionLab(
            self.champion_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        )
        champion_lab.run()
        champion_package = ChampionDecisionPackageManager().load_file(
            champion_lab.package_path
        )
        drift = _ReleaseScenario(
            self.root / "drift",
            scenario_id="drift",
            champion_package=champion_package,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
            final_profile="drift",
        ).run()
        passing = _ReleaseScenario(
            self.root / "passing",
            scenario_id="passing",
            champion_package=champion_package,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
            final_profile="passing",
        ).run()
        return ShadowCanaryReleaseLabResult(
            run_id=self.RUN_ID,
            resumed=drift.resumed and passing.resumed,
            champion_package_hash=champion_package.package_hash,
            incumbent_snapshot_id=champion_package.decision.baseline_snapshot_id,
            challenger_snapshot_id=champion_package.active_snapshot_id,
            drift=drift,
            passing=passing,
        )


class _ReleaseScenario:
    PLAN_CREATOR = "release-policy-owner"
    RELEASE_APPROVER_A = "release-reviewer-a"
    RELEASE_APPROVER_B = "release-reviewer-b"
    STAGE_OPERATOR = "release-stage-operator"
    EVIDENCE_PRODUCER = "synthetic-serving-observer"
    DECISION_ACTOR = "release-drift-evaluator"
    ROLLBACK_APPROVER_A = "rollback-reviewer-a"
    ROLLBACK_APPROVER_B = "rollback-reviewer-b"
    ROLLBACK_OPERATOR = "rollback-operator"
    START = datetime(2026, 8, 11, 11, 0, tzinfo=timezone.utc)

    def __init__(
        self,
        root: Path,
        *,
        scenario_id: str,
        champion_package,
        source_commit: str,
        source_repository: str,
        final_profile: Literal["drift", "passing"],
    ):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.scenario_id = scenario_id
        self.champion_package = champion_package
        self.source_commit = source_commit
        self.source_repository = source_repository
        self.final_profile = final_profile

    @property
    def evidence_root(self) -> Path:
        return self.root / "evidence"

    @property
    def release_database(self) -> Path:
        return self.root / "release-registry.db"

    @property
    def campaign_database(self) -> Path:
        return self.root / "release-campaigns.db"

    @property
    def package_path(self) -> Path:
        return self.root / "release-evidence-package.json"

    def run(self) -> ReleaseScenarioResult:
        plan = self._plan()
        package_existed = self.package_path.exists()
        manager = ReleaseEvidencePackageManager()
        if package_existed:
            package = manager.load_file(self.package_path)
            if package.plan != plan or package.champion_package != self.champion_package:
                raise RuntimeError("Read-only release resume differs from frozen inputs.")
            self._verify_persistent_state(package)
            return self._result(package, resumed=True)

        registry = SQLiteReleaseRegistry(self.release_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        lifecycle = ReleaseLifecycleService(
            registry=registry,
            campaign_governance=CampaignGovernanceService(campaigns),
        )
        submission = lifecycle.submit_plan(self.champion_package, plan)
        release_campaign = lifecycle.approve_release(
            submission.campaign.campaign_id,
            actor_id=self.RELEASE_APPROVER_A,
            reason="Independent release-plan review passed.",
            expected_revision=submission.campaign.revision,
        )
        release_campaign = lifecycle.approve_release(
            release_campaign.campaign_id,
            actor_id=self.RELEASE_APPROVER_B,
            reason="Independent serving-safety review passed.",
            expected_revision=release_campaign.revision,
        )
        if release_campaign.state != CampaignState.AUTHORIZED:
            raise RuntimeError("Champion release Campaign did not authorize.")
        head = lifecycle.synchronize_release_authorization(
            plan_id=plan.plan_id,
            campaign_id=release_campaign.campaign_id,
            actor_id="release-authorization-sync",
        )
        if head.state != ReleaseState.AUTHORIZED:
            raise RuntimeError("Release Registry did not synchronize authorization.")
        head = lifecycle.start_shadow(
            plan_id=plan.plan_id,
            campaign_id=release_campaign.campaign_id,
            expected_revision=head.revision,
            actor_id=self.STAGE_OPERATOR,
        )
        if head.state != ReleaseState.SHADOW:
            raise RuntimeError("Release plan did not start in shadow state.")

        importer = ReleaseEvidenceImporter(self.evidence_root)
        decisions = []
        assessments = []
        batches = []
        rollback_campaign = None
        for stage in plan.stages:
            path, digest = self._write_or_verify_evidence(plan, stage)
            batch = importer.import_file(
                str(path.relative_to(self.evidence_root)).replace("\\", "/"),
                expected_sha256=digest,
                plan=plan,
            )
            evaluation = lifecycle.evaluate_stage(
                plan.plan_id,
                batch,
                assessment_id=f"release-assessment:{self.scenario_id}:{stage.stage_id}",
                decision_id=f"release-decision:{self.scenario_id}:{stage.stage_id}",
                decision_actor_id=self.DECISION_ACTOR,
                decided_at=self.START + timedelta(hours=stage.stage_index + 1),
            )
            batches.append(evaluation.batch)
            assessments.append(evaluation.assessment)
            decisions.append(evaluation.decision)
            if evaluation.decision.action == ReleaseDecisionAction.ADVANCE:
                head = lifecycle.advance(
                    evaluation.decision.decision_id,
                    expected_revision=head.revision,
                    actor_id=self.STAGE_OPERATOR,
                )
            elif evaluation.decision.action == ReleaseDecisionAction.READY:
                head = lifecycle.mark_ready(
                    evaluation.decision.decision_id,
                    expected_revision=head.revision,
                    actor_id=self.STAGE_OPERATOR,
                )
            elif evaluation.decision.action == ReleaseDecisionAction.ROLLBACK:
                rollback_submission = lifecycle.submit_rollback(
                    self.champion_package,
                    evaluation.decision.decision_id,
                    expected_revision=head.revision,
                    actor_id=self.DECISION_ACTOR,
                )
                rollback_campaign = lifecycle.approve_rollback(
                    rollback_submission.campaign.campaign_id,
                    actor_id=self.ROLLBACK_APPROVER_A,
                    reason="Protected-segment rollback evidence verified.",
                    expected_revision=rollback_submission.campaign.revision,
                )
                rollback_campaign = lifecycle.approve_rollback(
                    rollback_campaign.campaign_id,
                    actor_id=self.ROLLBACK_APPROVER_B,
                    reason="Independent safety rollback review passed.",
                    expected_revision=rollback_campaign.revision,
                )
                if rollback_campaign.state != CampaignState.AUTHORIZED:
                    raise RuntimeError("Champion rollback Campaign did not authorize.")
                head = lifecycle.execute_rollback(
                    plan_id=plan.plan_id,
                    decision_id=evaluation.decision.decision_id,
                    campaign_id=rollback_campaign.campaign_id,
                    expected_revision=rollback_submission.head.revision,
                    actor_id=self.ROLLBACK_OPERATOR,
                )
            else:
                raise RuntimeError("Controlled release unexpectedly entered hold.")

        release_campaign = campaigns.get(release_campaign.campaign_id)
        if self.final_profile == "drift":
            if (
                head.state != ReleaseState.ROLLED_BACK
                or rollback_campaign is None
                or decisions[-1].action != ReleaseDecisionAction.ROLLBACK
                or "maximum_safety_violations_exceeded" not in assessments[-1].reasons
                or "protected_segment_regression:protected" not in assessments[-1].reasons
            ):
                raise RuntimeError("Drift control did not produce exact rollback evidence.")
        else:
            if (
                head.state != ReleaseState.READY
                or rollback_campaign is not None
                or decisions[-1].action != ReleaseDecisionAction.READY
            ):
                raise RuntimeError("Passing control did not reach local readiness.")

        registry.verify_state()
        campaigns.verify_audit()
        package = manager.build(
            package_id=f"release-evidence-package:{self.scenario_id}",
            created_at=datetime.now(timezone.utc),
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            champion_package=self.champion_package,
            plan=plan,
            batches=tuple(registry.list_batches(plan.plan_id)),
            assessments=tuple(registry.list_assessments(plan.plan_id)),
            decisions=tuple(registry.list_decisions(plan.plan_id)),
            release_campaign=release_campaign,
            release_approvals=tuple(campaigns.approvals(release_campaign.campaign_id)),
            rollback_campaign=(
                campaigns.get(rollback_campaign.campaign_id)
                if rollback_campaign is not None
                else None
            ),
            rollback_approvals=(
                tuple(campaigns.approvals(rollback_campaign.campaign_id))
                if rollback_campaign is not None
                else ()
            ),
            final_head=registry.head(plan.plan_id),
            release_events=tuple(registry.events()),
            release_checkpoint=registry.checkpoint(),
            campaign_events=tuple(campaigns.audit_events()),
            campaign_checkpoint=campaigns.checkpoint(),
        )
        manager.export_file(package, self.package_path)
        self._verify_persistent_state(package)
        return self._result(package, resumed=False)

    def _plan(self):
        segments = (
            build_release_segment("general", protected=False),
            build_release_segment("protected", protected=True),
        )
        stages = (
            build_release_stage(
                stage_id="shadow",
                stage_index=0,
                kind=ReleaseStageKind.SHADOW,
                candidate_traffic_percent=0.0,
                minimum_pairs=8,
                minimum_pairs_per_segment=4,
                observation_window_seconds=3600,
            ),
            build_release_stage(
                stage_id="canary-10",
                stage_index=1,
                kind=ReleaseStageKind.CANARY,
                candidate_traffic_percent=10.0,
                minimum_pairs=8,
                minimum_pairs_per_segment=4,
                observation_window_seconds=3600,
            ),
            build_release_stage(
                stage_id="canary-25",
                stage_index=2,
                kind=ReleaseStageKind.CANARY,
                candidate_traffic_percent=25.0,
                minimum_pairs=8,
                minimum_pairs_per_segment=4,
                observation_window_seconds=3600,
            ),
        )
        policy = build_release_policy(
            policy_id=f"release-policy:{self.scenario_id}:strict-v1",
            minimum_success_rate_delta=-0.05,
            bootstrap_confidence=0.80,
            bootstrap_resamples=1024,
            bootstrap_seed=41,
            minimum_delta_lower_bound=-0.50,
            maximum_error_rate_delta=0.10,
            maximum_safety_violations=0,
            maximum_p95_latency_growth_ratio=0.25,
            maximum_input_token_growth_ratio=0.25,
            maximum_output_token_growth_ratio=0.25,
            maximum_cost_growth_ratio=0.25,
            maximum_regressed_segments=0,
            maximum_regressed_segment_fraction=0.0,
            protected_segment_zero_regression=True,
            require_token_evidence=True,
            require_cost_evidence=True,
        )
        return build_release_plan(
            plan_id=f"release-plan:{self.scenario_id}:a0-to-a1",
            champion_package_hash=self.champion_package.package_hash,
            family_id=self.champion_package.active_family_id,
            incumbent_snapshot_id=(
                self.champion_package.decision.baseline_snapshot_id
            ),
            challenger_snapshot_id=self.champion_package.active_snapshot_id,
            champion_decision_hash=self.champion_package.decision.decision_hash,
            runtime_config_sha256=canonical_sha256(
                {"runtime": "synthetic-release-runtime-v1", "scenario": self.scenario_id}
            ),
            tool_contract_sha256=canonical_sha256(
                {"tools": ["synthetic-observe"], "version": 1}
            ),
            segments=segments,
            stages=stages,
            policy=policy,
            evidence_source=ReleaseEvidenceSource.SYNTHETIC_FIXTURE,
            created_by=self.PLAN_CREATOR,
            created_at=self.START,
            source_commit=self.source_commit,
        )

    def _write_or_verify_evidence(self, plan, stage):
        target = self.evidence_root / stage.stage_id / "release-evidence.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = self._evidence_payload(plan, stage)
        encoded = (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")
        digest = hashlib.sha256(encoded).hexdigest()
        if target.exists():
            if target.is_symlink() or target.read_bytes() != encoded:
                raise RuntimeError("Persisted synthetic release evidence drifted.")
        else:
            target.write_bytes(encoded)
        return target, digest

    def _evidence_payload(self, plan, stage):
        incumbent = plan.incumbent_snapshot_id
        challenger = plan.challenger_snapshot_id
        incumbent_outcomes = {
            "general": (True, True, True, False),
            "protected": (True, True, True, True),
        }
        challenger_outcomes = {
            "general": (True, True, True, True),
            "protected": (
                (True, True, True, False)
                if self.final_profile == "drift" and stage.stage_id == "canary-25"
                else (True, True, True, True)
            ),
        }
        start = self.START + timedelta(hours=stage.stage_index)
        events = []
        pair_index = 0
        for segment_id in ("general", "protected"):
            for item_index, (incumbent_success, challenger_success) in enumerate(
                zip(
                    incumbent_outcomes[segment_id],
                    challenger_outcomes[segment_id],
                    strict=True,
                ),
                start=1,
            ):
                pair_index += 1
                pair_id = f"pair:{self.scenario_id}:{stage.stage_id}:{pair_index}"
                observed = start + timedelta(minutes=pair_index * 3)
                events.append(
                    self._raw_event(
                        event_id=f"event:{pair_id}:incumbent",
                        pair_id=pair_id,
                        stage_id=stage.stage_id,
                        segment_id=segment_id,
                        snapshot_id=incumbent,
                        success=incumbent_success,
                        safety_violation=False,
                        latency_ms=100.0 + item_index,
                        input_tokens=100,
                        output_tokens=40,
                        cost_usd=0.010,
                        observed_at=observed,
                    )
                )
                safety_violation = (
                    self.final_profile == "drift"
                    and stage.stage_id == "canary-25"
                    and segment_id == "protected"
                    and item_index == 4
                )
                events.append(
                    self._raw_event(
                        event_id=f"event:{pair_id}:challenger",
                        pair_id=pair_id,
                        stage_id=stage.stage_id,
                        segment_id=segment_id,
                        snapshot_id=challenger,
                        success=challenger_success and not safety_violation,
                        safety_violation=safety_violation,
                        latency_ms=108.0 + item_index,
                        input_tokens=108,
                        output_tokens=43,
                        cost_usd=0.011,
                        observed_at=observed + timedelta(seconds=1),
                    )
                )
        return {
            "batch_id": f"release-batch:{self.scenario_id}:{stage.stage_id}",
            "plan_id": plan.plan_id,
            "plan_hash": plan.plan_hash,
            "stage_id": stage.stage_id,
            "incumbent_snapshot_id": incumbent,
            "challenger_snapshot_id": challenger,
            "candidate_traffic_percent": stage.candidate_traffic_percent,
            "window_start": start.isoformat(),
            "window_end": (start + timedelta(minutes=30)).isoformat(),
            "producer_id": self.EVIDENCE_PRODUCER,
            "declared_event_count": len(events),
            "declared_pair_count": len(events) // 2,
            "events": events,
        }

    @staticmethod
    def _raw_event(
        *,
        event_id,
        pair_id,
        stage_id,
        segment_id,
        snapshot_id,
        success,
        safety_violation,
        latency_ms,
        input_tokens,
        output_tokens,
        cost_usd,
        observed_at,
    ):
        return {
            "event_id": event_id,
            "pair_id": pair_id,
            "stage_id": stage_id,
            "segment_id": segment_id,
            "snapshot_id": snapshot_id,
            "success": success,
            "error": False,
            "safety_violation": safety_violation,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": cost_usd,
            "observed_at": observed_at.isoformat(),
        }

    def _verify_persistent_state(self, package):
        registry = SQLiteReleaseRegistry(self.release_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        registry.verify_audit(package.release_checkpoint)
        registry.verify_state()
        campaigns.verify_audit(package.campaign_checkpoint)
        if (
            registry.get_plan(package.plan.plan_id) != package.plan
            or tuple(registry.list_batches(package.plan.plan_id)) != package.batches
            or tuple(registry.list_assessments(package.plan.plan_id))
            != package.assessments
            or tuple(registry.list_decisions(package.plan.plan_id))
            != package.decisions
            or registry.head(package.plan.plan_id) != package.final_head
            or tuple(registry.events()) != package.release_events
            or campaigns.get(package.release_campaign.campaign_id)
            != package.release_campaign
            or tuple(campaigns.approvals(package.release_campaign.campaign_id))
            != package.release_approvals
            or tuple(campaigns.audit_events()) != package.campaign_events
        ):
            raise RuntimeError("Persistent release state differs from its package.")
        if package.rollback_campaign is not None:
            if (
                campaigns.get(package.rollback_campaign.campaign_id)
                != package.rollback_campaign
                or tuple(campaigns.approvals(package.rollback_campaign.campaign_id))
                != package.rollback_approvals
            ):
                raise RuntimeError("Persistent rollback state differs from package.")

    def _result(self, package, *, resumed: bool):
        decisions_by_stage = {item.stage_id: item for item in package.decisions}
        assessments_by_stage = {item.stage_id: item for item in package.assessments}
        final_stage = package.plan.stages[-1].stage_id
        final_decision = decisions_by_stage[final_stage]
        return ReleaseScenarioResult(
            scenario_id=self.scenario_id,
            resumed=resumed,
            plan_id=package.plan.plan_id,
            plan_hash=package.plan.plan_hash,
            actions=tuple(
                decisions_by_stage[item.stage_id].action.value
                for item in package.plan.stages
            ),
            assessment_statuses=tuple(
                assessments_by_stage[item.stage_id].status.value
                for item in package.plan.stages
            ),
            final_state=package.final_head.state.value,
            final_primary_snapshot_id=package.final_head.primary_snapshot_id,
            final_active_stage_id=package.final_head.active_stage_id,
            final_candidate_allocation_percent=(
                package.final_head.candidate_allocation_percent
            ),
            final_revision=package.final_head.revision,
            release_campaign_id=package.release_campaign.campaign_id,
            release_campaign_state=package.release_campaign.state.value,
            release_approval_count=len(package.release_approvals),
            rollback_campaign_id=(
                package.rollback_campaign.campaign_id
                if package.rollback_campaign is not None
                else None
            ),
            rollback_campaign_state=(
                package.rollback_campaign.state.value
                if package.rollback_campaign is not None
                else None
            ),
            rollback_approval_count=len(package.rollback_approvals),
            rollback_reasons=(
                assessments_by_stage[final_stage].reasons
                if final_decision.action == ReleaseDecisionAction.ROLLBACK
                else ()
            ),
            batch_count=len(package.batches),
            release_event_count=len(package.release_events),
            campaign_event_count=len(package.campaign_events),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "ReleaseScenarioResult",
    "ShadowCanaryReleaseLab",
    "ShadowCanaryReleaseLabResult",
]
