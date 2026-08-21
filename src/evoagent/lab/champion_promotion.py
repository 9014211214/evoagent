from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.benchmark_evidence.package import (
    BenchmarkComparisonPackageManager,
)
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignState,
    SQLiteCampaignRepository,
)
from evoagent.champion import (
    ChampionDecisionPackageManager,
    ChampionLifecycleService,
    ChampionRoundStatus,
    ChampionStopRecommendation,
    ChampionVersionStatus,
    SQLiteChampionRegistry,
    build_champion_policy,
)
from evoagent.lab.benchmark_evidence import AuthoritativeBenchmarkEvidenceLab
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH


class BenchmarkGatedChampionLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    benchmark_package_hash: str
    decision_id: str
    decision_hash: str
    policy_hash: str
    baseline_score: float
    a1_score: float
    a2_score: float
    selected_run_id: str
    selected_snapshot_id: str
    selected_round: int = Field(ge=1)
    selected_score: float
    a1_status: str
    a2_status: str
    a2_reasons: tuple[str, ...]
    stop_recommendation: str
    continue_evolution: bool
    bootstrap_lower_bound: float
    bootstrap_upper_bound: float
    approval_count: int = Field(ge=0)
    required_approvals: int = Field(ge=1)
    campaign_id: str
    campaign_state: str
    active_snapshot_id: str
    active_revision: int = Field(ge=0)
    champion_record_count: int = Field(ge=0)
    champion_event_count: int = Field(ge=0)
    campaign_event_count: int = Field(ge=0)
    package_path: str
    package_hash: str
    second_run_read_only: Literal[True] = True
    synthetic_fixture: Literal[True] = True
    harbor_execution_performed_by_evoagent: Literal[False] = False
    external_model_call_performed_by_evoagent: Literal[False] = False
    training_executed_by_evoagent: Literal[False] = False
    checkpoint_downloaded_or_loaded: Literal[False] = False
    upload_performed: Literal[False] = False
    official_submission_performed: Literal[False] = False
    official_submission_accepted: Literal[False] = False
    production_deployment_performed: Literal[False] = False


class BenchmarkGatedChampionLab:
    """Select A1 over higher-scoring A2 when A2 violates zero-regression policy."""

    RUN_ID = "benchmark-gated-champion-lab-v1"
    PACKAGE_ID = "champion-decision-package-v1"
    DECISION_ID = "champion-decision:zero-regression-a0-a2"
    DECISION_ACTOR = "champion-policy-evaluator"
    APPROVER_A = "champion-reviewer-a"
    APPROVER_B = "champion-reviewer-b"
    ACTIVATOR = "champion-operator"
    DECIDED_AT = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)

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
            raise ValueError("Champion lab root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef"
            for character in source_commit
        ):
            raise ValueError(
                "source_commit must be lowercase 40-character Git hex."
            )
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository

    @property
    def benchmark_root(self) -> Path:
        return self.root / "benchmark-evidence"

    @property
    def champion_database(self) -> Path:
        return self.root / "champion-registry.db"

    @property
    def campaign_database(self) -> Path:
        return self.root / "champion-campaigns.db"

    @property
    def package_path(self) -> Path:
        return self.root / "champion-decision-package.json"

    def run(self) -> BenchmarkGatedChampionLabResult:
        benchmark_lab = AuthoritativeBenchmarkEvidenceLab(
            self.benchmark_root,
            source_commit=self.source_commit,
            source_repository=self.source_repository,
        )
        benchmark_result = benchmark_lab.run()
        benchmark_package = BenchmarkComparisonPackageManager().load_file(
            benchmark_lab.package_path
        )
        package_existed = self.package_path.exists()
        if package_existed:
            package = ChampionDecisionPackageManager().load_file(
                self.package_path
            )
            if package.benchmark_package != benchmark_package:
                raise RuntimeError(
                    "Read-only Champion resume differs from benchmark evidence."
                )
            self._verify_persistent_state(package)
            return self._result(package, resumed=True)

        by_id = {
            item.evidence_id: item for item in benchmark_package.runs
        }
        baseline = by_id[benchmark_package.longitudinal.run_ids[0]]
        registry = SQLiteChampionRegistry(self.champion_database)
        try:
            registry.active(baseline.contract.agent.family_id)
        except KeyError:
            registry.register_initial(
                baseline,
                benchmark_package_hash=benchmark_package.package_hash,
            )
        if registry.active(baseline.contract.agent.family_id).snapshot_id != (
            baseline.contract.agent.snapshot_id
        ):
            raise RuntimeError("Champion lab baseline pointer is not A0.")

        campaign_repository = SQLiteCampaignRepository(self.campaign_database)
        lifecycle = ChampionLifecycleService(
            registry=registry,
            campaign_governance=CampaignGovernanceService(
                campaign_repository
            ),
        )
        policy = build_champion_policy(
            minimum_score_gain=0.10,
            minimum_gain_lower_bound=0.0,
            maximum_regressed_tasks=0,
            maximum_regression_fraction=0.0,
            maximum_error_rate_delta=0.0,
            maximum_input_token_growth_ratio=0.50,
            maximum_output_token_growth_ratio=0.50,
            maximum_cost_growth_ratio=0.50,
            require_token_evidence=True,
            require_cost_evidence=True,
            allow_non_final_round=True,
            patience_rounds=1,
        )
        submission = lifecycle.evaluate_and_submit(
            benchmark_package,
            policy=policy,
            decision_id=self.DECISION_ID,
            decision_actor_id=self.DECISION_ACTOR,
            decided_at=self.DECIDED_AT,
        )
        if (
            submission.selected_run is None
            or submission.selected_assessment is None
            or submission.campaign is None
            or submission.record is None
        ):
            raise RuntimeError("Controlled Champion policy did not select a Challenger.")
        decision = submission.decision
        assessments = {
            item.evolution_round: item for item in decision.assessments
        }
        if (
            decision.selected_round != 1
            or decision.selected_run_id != "benchmark-run:a1"
            or decision.selected_snapshot_id != "evoagent-a1"
            or assessments[1].status != ChampionRoundStatus.ELIGIBLE
            or assessments[2].status != ChampionRoundStatus.REJECTED
            or "maximum_regressed_tasks_exceeded"
            not in assessments[2].reasons
            or decision.stop_recommendation
            != ChampionStopRecommendation.STOP
        ):
            raise RuntimeError(
                "Zero-regression Champion policy did not select A1 and reject A2."
            )
        family_id = baseline.contract.agent.family_id
        if (
            registry.active(family_id).snapshot_id
            != baseline.contract.agent.snapshot_id
            or registry.active_revision(family_id) != 0
        ):
            raise RuntimeError(
                "Champion evaluation changed the active pointer before approval."
            )

        campaign = submission.campaign
        campaign = lifecycle.approve(
            campaign.campaign_id,
            actor_id=self.APPROVER_A,
            reason="Independent benchmark and regression review passed.",
            expected_revision=campaign.revision,
        )
        campaign = lifecycle.approve(
            campaign.campaign_id,
            actor_id=self.APPROVER_B,
            reason="Independent governance and resource review passed.",
            expected_revision=campaign.revision,
        )
        if campaign.state != CampaignState.AUTHORIZED:
            raise RuntimeError("Champion Campaign did not reach AUTHORIZED.")
        if registry.active(family_id).snapshot_id != baseline.contract.agent.snapshot_id:
            raise RuntimeError(
                "Campaign authorization silently activated the Challenger."
            )
        authorized = lifecycle.synchronize_authorization(
            family_id=family_id,
            snapshot_id=decision.selected_snapshot_id,
            campaign_id=campaign.campaign_id,
            actor_id="champion-authorization-sync",
        )
        if (
            authorized.status != ChampionVersionStatus.AUTHORIZED
            or registry.active(family_id).snapshot_id
            != baseline.contract.agent.snapshot_id
            or registry.active_revision(family_id) != 0
        ):
            raise RuntimeError(
                "Registry authorization changed the active Champion pointer."
            )
        active = lifecycle.activate(
            family_id=family_id,
            snapshot_id=decision.selected_snapshot_id,
            campaign_id=campaign.campaign_id,
            expected_active_revision=0,
            actor_id=self.ACTIVATOR,
        )
        campaign = campaign_repository.get(campaign.campaign_id)
        if (
            active.status != ChampionVersionStatus.CHAMPION
            or registry.active(family_id).snapshot_id
            != decision.selected_snapshot_id
            or registry.active_revision(family_id) != 1
            or campaign.state != CampaignState.COMPLETED
        ):
            raise RuntimeError("Explicit Champion activation did not complete exactly.")

        registry.verify_state()
        campaign_repository.verify_audit()
        package = ChampionDecisionPackageManager().build(
            package_id=self.PACKAGE_ID,
            created_at=datetime.now(timezone.utc),
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
            benchmark_package=benchmark_package,
            policy=policy,
            decision=decision,
            promotion_campaign=campaign,
            approvals=tuple(
                campaign_repository.approvals(campaign.campaign_id)
            ),
            champion_records=tuple(registry.list_snapshots(family_id)),
            champion_events=tuple(registry.events()),
            champion_checkpoint=registry.checkpoint(),
            campaign_events=tuple(campaign_repository.audit_events()),
            campaign_checkpoint=campaign_repository.checkpoint(),
            active_family_id=family_id,
            active_snapshot_id=registry.active(family_id).snapshot_id,
            active_revision=registry.active_revision(family_id),
        )
        ChampionDecisionPackageManager().export_file(
            package,
            self.package_path,
        )
        self._verify_persistent_state(package)
        if benchmark_result.harbor_execution_performed_by_evoagent:
            raise RuntimeError("Champion lab unexpectedly executed Harbor.")
        return self._result(package, resumed=False)

    def _verify_persistent_state(self, package) -> None:
        registry = SQLiteChampionRegistry(self.champion_database)
        campaign_repository = SQLiteCampaignRepository(
            self.campaign_database
        )
        registry.verify_audit(package.champion_checkpoint)
        registry.verify_state()
        campaign_repository.verify_audit(package.campaign_checkpoint)
        if (
            tuple(registry.list_decisions()) != (package.decision,)
            or tuple(registry.list_snapshots(package.active_family_id))
            != package.champion_records
            or tuple(registry.events()) != package.champion_events
            or registry.active(package.active_family_id).snapshot_id
            != package.active_snapshot_id
            or registry.active_revision(package.active_family_id)
            != package.active_revision
            or campaign_repository.get(
                package.promotion_campaign.campaign_id
            )
            != package.promotion_campaign
            or tuple(
                campaign_repository.approvals(
                    package.promotion_campaign.campaign_id
                )
            )
            != package.approvals
            or tuple(campaign_repository.audit_events())
            != package.campaign_events
        ):
            raise RuntimeError(
                "Persistent Champion state differs from the reproducible package."
            )

    def _result(
        self,
        package,
        *,
        resumed: bool,
    ) -> BenchmarkGatedChampionLabResult:
        assessments = {
            item.evolution_round: item
            for item in package.decision.assessments
        }
        points = {
            item.evolution_round: item
            for item in package.benchmark_package.longitudinal.points
        }
        selected = assessments[package.decision.selected_round]
        return BenchmarkGatedChampionLabResult(
            run_id=self.RUN_ID,
            resumed=resumed,
            benchmark_package_hash=package.benchmark_package.package_hash,
            decision_id=package.decision.decision_id,
            decision_hash=package.decision.decision_hash,
            policy_hash=package.policy.policy_hash,
            baseline_score=points[0].score,
            a1_score=points[1].score,
            a2_score=points[2].score,
            selected_run_id=package.decision.selected_run_id,
            selected_snapshot_id=package.decision.selected_snapshot_id,
            selected_round=package.decision.selected_round,
            selected_score=selected.score,
            a1_status=assessments[1].status.value,
            a2_status=assessments[2].status.value,
            a2_reasons=assessments[2].reasons,
            stop_recommendation=(
                package.decision.stop_recommendation.value
            ),
            continue_evolution=package.decision.continue_evolution,
            bootstrap_lower_bound=selected.bootstrap.lower_bound,
            bootstrap_upper_bound=selected.bootstrap.upper_bound,
            approval_count=len(package.approvals),
            required_approvals=package.promotion_campaign.required_approvals,
            campaign_id=package.promotion_campaign.campaign_id,
            campaign_state=package.promotion_campaign.state.value,
            active_snapshot_id=package.active_snapshot_id,
            active_revision=package.active_revision,
            champion_record_count=len(package.champion_records),
            champion_event_count=len(package.champion_events),
            campaign_event_count=len(package.campaign_events),
            package_path=str(self.package_path),
            package_hash=package.package_hash,
        )


__all__ = [
    "BenchmarkGatedChampionLab",
    "BenchmarkGatedChampionLabResult",
]
