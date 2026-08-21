from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent import __version__
from evoagent.campaigns import (
    CampaignGovernanceService,
    CampaignOperatorView,
    CampaignState,
    CampaignType,
    PersistentModelEvidenceAccumulator,
    SQLiteCampaignRepository,
)
from evoagent.campaigns.cycle import GovernedEvolutionCycleService
from evoagent.cycles import (
    CycleStatus,
    EvolutionCyclePolicy,
    EvolutionCycleRequest,
    ModelEvolutionSettings,
    StructuredVerifierSkillBackend,
)
from evoagent.diagnosis import AttributionReport, CounterfactualAttributionEngine
from evoagent.domain.models import EvolutionAction, FailureLayer, Task
from evoagent.lab.automatic_local_tool import IdempotentJsonlTraceStore
from evoagent.lab.service import DEFAULT_THIRD_PARTY_LOCK_HASH
from evoagent.runtime import (
    ExecutableCrossLayerCounterfactualRunner,
    ExecutableFaultScenario,
)
from evoagent.skills import SkillRegistry
from evoagent.traces import TraceTrustLevel
from evoagent.training import (
    AgenticRLEnvironmentSpec,
    AgenticRLPlanner,
    DatasetSignals,
    DryRunAgenticRLBackend,
    MetricTarget,
    ModelCandidate,
    ModelEvidenceDatasetManager,
    ModelEvidenceDatasetManifest,
    ModelEvidenceExample,
    ModelEvolutionPackageManager,
    ModelEvolutionPackageManifest,
    ModelImprovementTicket,
    RewardComponent,
    RewardSpec,
    RLAlgorithm,
    TrainingBudget,
    TrainingMethod,
)


class ExecutableModelEvidenceCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    task_id: str
    baseline_trace_id: str
    reference_trace_id: str
    attribution: AttributionReport
    supported_experiments: tuple[str, ...]


class GovernedModelEvolutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    evidence_cases: tuple[ExecutableModelEvidenceCase, ...]
    follow_up_case: ExecutableModelEvidenceCase
    evidence_task_ids: tuple[str, ...]
    held_out_tasks: tuple[Task, ...]
    held_out_baseline_passed: tuple[bool, ...]
    held_out_reference_passed: tuple[bool, ...]
    cycle_statuses: tuple[CycleStatus, ...]
    follow_up_status: CycleStatus
    campaign_id: str
    campaign_state: str
    campaign_count: int = Field(ge=0)
    approval_count: int = Field(ge=0)
    persisted_trace_count: int = Field(ge=0)
    dataset_path: str
    dataset_manifest_hash: str
    supervised_example_count: int = Field(ge=0)
    preference_pair_count: int = Field(ge=0)
    replay_seed_count: int = Field(ge=0)
    dataset_signals: DatasetSignals
    model_ticket: ModelImprovementTicket
    model_candidate: ModelCandidate
    package_path: str
    package_hash: str
    campaign_checkpoint: dict[str, Any]
    trace_checkpoint: dict[str, Any]
    restart_verified: Literal[True] = True
    training_executed: Literal[False] = False
    external_execution_performed: Literal[False] = False


class _FrozenTaskModelRunner(ExecutableCrossLayerCounterfactualRunner):
    def __init__(self, *, root: Path, scenario: ExecutableFaultScenario, task: Task):
        self._frozen_model_task = task.model_copy(deep=True)
        super().__init__(root=root, scenario=scenario)

    def _create_task(self) -> Task:
        return self._frozen_model_task.model_copy(deep=True)


class GovernedModelEvolutionLab:
    """Aggregate executable Model failures into a non-executing governed package."""

    RUN_ID = "governed-model-evolution-package-v1"
    PROBLEM_CLUSTER = "local-document-action-generation"
    ENVIRONMENT_ID = "local-document-environment-v1"
    VERIFIER_ID = "local-document-verifier-v1"
    BASE_MODEL_ID = "synthetic/incapable-local-document-policy-v1"
    REFERENCE_MODEL_ID = "synthetic/reference-local-document-policy-v1"
    EVIDENCE_COUNT = 4
    FOLLOW_UP_INDEX = 5
    REPLAY_SEED = 53
    DATASET_CREATED_AT = datetime(2026, 8, 10, 15, 0, tzinfo=timezone.utc)
    PACKAGE_CREATED_AT = datetime(2026, 8, 10, 15, 5, tzinfo=timezone.utc)

    def __init__(
        self,
        root: str | Path,
        *,
        source_commit: str = "0" * 40,
        source_repository: str = "https://github.com/9014211214/evoagent",
    ):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Governed model-evolution root must not be a symlink.")
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef" for character in source_commit
        ):
            raise ValueError("source_commit must be lowercase 40-character Git hex.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository
        self.engine = CounterfactualAttributionEngine()

    @property
    def campaign_database(self) -> Path:
        return self.root / "campaigns.db"

    @property
    def trace_file(self) -> Path:
        return self.root / "traces.jsonl"

    @property
    def dataset_path(self) -> Path:
        return self.root / "model-evidence-dataset.json"

    @property
    def package_path(self) -> Path:
        return self.root / "model-evolution-package.json"

    def run(self) -> GovernedModelEvolutionResult:
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        traces = IdempotentJsonlTraceStore(self.trace_file)
        existing_campaigns = CampaignOperatorView(campaigns).list_campaigns(
            campaign_type=CampaignType.MODEL
        )
        if len(existing_campaigns) > 1:
            raise RuntimeError("More than one Model Campaign exists for the governed lab.")
        resumed = bool(existing_campaigns)

        evidence_runners = tuple(
            self._runner(index=index, task=self._evidence_task(index))
            for index in range(1, self.EVIDENCE_COUNT + 1)
        )
        evidence_cases: list[ExecutableModelEvidenceCase] = []
        examples: list[ModelEvidenceExample] = []
        request_traces = []
        for runner in evidence_runners:
            report = self.engine.diagnose(runner)
            self._validate_model_attribution(report)
            failed = self._persisted_or_generated_trace(traces, runner.baseline_trace)
            reference = runner.traces()["exp:model"]
            example = ModelEvidenceExample.build(
                report=report,
                failed_trace=failed,
                reference_trace=reference,
                problem_cluster=self.PROBLEM_CLUSTER,
            )
            examples.append(example)
            request_traces.append(failed)
            evidence_cases.append(self._case(report, failed.trace_id, reference.trace_id))

        held_out_tasks = self._held_out_tasks()
        held_out_baseline, held_out_reference = self._validate_held_out_tasks(held_out_tasks)
        dataset_manager = ModelEvidenceDatasetManager()
        dataset = dataset_manager.build(
            examples=tuple(examples),
            held_out_task_ids=tuple(task.task_id for task in held_out_tasks),
            environment_id=self.ENVIRONMENT_ID,
            verifier_id=self.VERIFIER_ID,
            created_at=self.DATASET_CREATED_AT,
            replay_seed=self.REPLAY_SEED,
        )
        if self.dataset_path.exists():
            stored_dataset = dataset_manager.load_file(self.dataset_path)
            if stored_dataset != dataset:
                raise RuntimeError("Persisted model evidence dataset differs from replayed evidence.")
        else:
            dataset_manager.export_file(dataset, self.dataset_path)
        dataset_manager.verify(dataset)
        signals = dataset_manager.signals(dataset)

        follow_up_runner = self._runner(
            index=self.FOLLOW_UP_INDEX,
            task=self._evidence_task(self.FOLLOW_UP_INDEX, follow_up=True),
        )
        follow_up_report = self.engine.diagnose(follow_up_runner)
        self._validate_model_attribution(follow_up_report)
        follow_up_trace = self._persisted_or_generated_trace(
            traces, follow_up_runner.baseline_trace
        )
        follow_up_reference = follow_up_runner.traces()["exp:model"]
        follow_up_case = self._case(
            follow_up_report,
            follow_up_trace.trace_id,
            follow_up_reference.trace_id,
        )

        settings = self._settings(dataset, signals)
        backend = self._backend(dataset)
        if resumed:
            campaign = existing_campaigns[0]
            ticket, candidate = self._campaign_artifacts(campaign)
            cycle_statuses = tuple(
                CycleStatus.MODEL_CANDIDATE for _ in range(self.EVIDENCE_COUNT)
            )
            follow_up_status = CycleStatus.MODEL_CANDIDATE
        else:
            service = GovernedEvolutionCycleService(
                trace_store=traces,
                skill_registry=SkillRegistry(),
                skill_backend=StructuredVerifierSkillBackend(),
                policy=EvolutionCyclePolicy(
                    model_min_traces=self.EVIDENCE_COUNT,
                    model_min_distinct_tasks=self.EVIDENCE_COUNT,
                ),
                evidence_accumulator=PersistentModelEvidenceAccumulator(campaigns),
                campaign_governance=CampaignGovernanceService(campaigns),
            )
            cycle_results = []
            for trace, runner in zip(request_traces, evidence_runners, strict=True):
                cycle_results.append(
                    service.process(
                        EvolutionCycleRequest(
                            trace=trace,
                            source="synthetic-governed-model-evidence",
                            trust_level=TraceTrustLevel.VERIFIED,
                            model_settings=settings,
                        ),
                        counterfactual_runner=runner,
                        model_backend=backend,
                    )
                )
            self._validate_threshold_results(cycle_results)
            final = cycle_results[-1]
            if (
                final.campaign_id is None
                or final.model_ticket is None
                or final.model_candidate is None
            ):
                raise RuntimeError("Threshold result omitted Campaign, Ticket, or Candidate.")

            follow_up = service.process(
                EvolutionCycleRequest(
                    trace=follow_up_trace,
                    source="synthetic-governed-model-evidence",
                    trust_level=TraceTrustLevel.VERIFIED,
                    model_settings=settings,
                ),
                counterfactual_runner=follow_up_runner,
                model_backend=backend,
            )
            if (
                follow_up.status != CycleStatus.MODEL_CANDIDATE
                or not follow_up.reused
                or follow_up.campaign_id != final.campaign_id
                or follow_up.model_candidate != final.model_candidate
                or follow_up.model_ticket != final.model_ticket
            ):
                raise RuntimeError(
                    "Follow-up Model evidence did not reuse the existing Campaign and Candidate."
                )
            campaign = campaigns.get(final.campaign_id)
            ticket = final.model_ticket
            candidate = final.model_candidate
            cycle_statuses = tuple(item.status for item in cycle_results)
            follow_up_status = follow_up.status

        self._validate_campaign(campaign, ticket, candidate, dataset)
        campaign_checkpoint = campaigns.checkpoint()
        trace_checkpoint = traces.checkpoint()
        package_manager = ModelEvolutionPackageManager()
        if self.package_path.exists():
            package = package_manager.load_file(self.package_path)
            self._validate_existing_package(
                package,
                campaign=campaign,
                dataset=dataset,
                ticket=ticket,
                candidate=candidate,
                held_out_tasks=held_out_tasks,
                campaign_checkpoint=campaign_checkpoint,
                trace_checkpoint=trace_checkpoint,
            )
        else:
            package = package_manager.build(
                run_id=self.RUN_ID,
                created_at=self.PACKAGE_CREATED_AT,
                framework_version=__version__,
                source_repository=self.source_repository,
                source_commit=self.source_commit,
                third_party_lock_hash=DEFAULT_THIRD_PARTY_LOCK_HASH,
                campaign=campaign,
                dataset=dataset,
                held_out_tasks=held_out_tasks,
                ticket=ticket,
                candidate=candidate,
                campaign_checkpoint=campaign_checkpoint,
                trace_checkpoint=trace_checkpoint,
            )
            package_manager.export_file(package, self.package_path)

        self._verify_restart(
            campaign=campaign,
            dataset=dataset,
            package=package,
            campaign_checkpoint=campaign_checkpoint,
            trace_checkpoint=trace_checkpoint,
        )
        current_campaigns = CampaignOperatorView(campaigns).list_campaigns(
            campaign_type=CampaignType.MODEL
        )
        return GovernedModelEvolutionResult(
            run_id=self.RUN_ID,
            resumed=resumed,
            evidence_cases=tuple(evidence_cases),
            follow_up_case=follow_up_case,
            evidence_task_ids=dataset.evidence_task_ids,
            held_out_tasks=held_out_tasks,
            held_out_baseline_passed=held_out_baseline,
            held_out_reference_passed=held_out_reference,
            cycle_statuses=cycle_statuses,
            follow_up_status=follow_up_status,
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.state.value,
            campaign_count=len(current_campaigns),
            approval_count=len(campaigns.approvals(campaign.campaign_id)),
            persisted_trace_count=len(traces.list()),
            dataset_path=str(self.dataset_path),
            dataset_manifest_hash=dataset.manifest_hash,
            supervised_example_count=len(dataset.supervised_examples),
            preference_pair_count=len(dataset.preference_pairs),
            replay_seed_count=len(dataset.replay_seeds),
            dataset_signals=signals,
            model_ticket=ticket,
            model_candidate=candidate,
            package_path=str(self.package_path),
            package_hash=package.package_hash,
            campaign_checkpoint=campaign_checkpoint.model_dump(mode="json"),
            trace_checkpoint=trace_checkpoint.model_dump(mode="json"),
        )

    def _runner(self, *, index: int, task: Task) -> _FrozenTaskModelRunner:
        return _FrozenTaskModelRunner(
            root=self.root / "counterfactual-episodes",
            scenario=ExecutableFaultScenario(
                scenario_id=f"model-evidence-{index}",
                fault_layers=(FailureLayer.MODEL,),
                seed=self.REPLAY_SEED,
            ),
            task=task,
        )

    def _evidence_task(self, index: int, *, follow_up: bool = False) -> Task:
        prefix = "model-follow-up" if follow_up else "model-evidence"
        return Task(
            task_id=f"{prefix}:{index}",
            task_type="governed-model-create-document",
            input={
                "initial_documents": {},
                "target_path": f"{prefix}-{index}.txt",
                "content": f"verified synthetic model example {index}",
                "expected_status": "completed",
                "require_verification": True,
            },
            expected_outcome={"status": "completed"},
            tags=["model-evidence", "synthetic", "local-tool"],
        )

    @staticmethod
    def _held_out_tasks() -> tuple[Task, ...]:
        return tuple(
            Task(
                task_id=f"model-held-out:{index}",
                task_type="governed-model-held-out-create-document",
                input={
                    "initial_documents": {},
                    "target_path": f"held-out-{index}.txt",
                    "content": f"frozen held-out model example {index}",
                    "expected_status": "completed",
                    "require_verification": True,
                },
                expected_outcome={"status": "completed"},
                tags=["held-out", "synthetic", "local-tool"],
            )
            for index in (1, 2)
        )

    def _validate_held_out_tasks(
        self, tasks: tuple[Task, ...]
    ) -> tuple[tuple[bool, ...], tuple[bool, ...]]:
        baseline: list[bool] = []
        reference: list[bool] = []
        for index, task in enumerate(tasks, start=1):
            runner = self._runner(index=100 + index, task=task)
            report = self.engine.diagnose(runner)
            self._validate_model_attribution(report)
            baseline.append(runner.baseline_trace.verifier_passed)
            reference.append(runner.traces()["exp:model"].verifier_passed)
        if any(baseline) or not all(reference):
            raise RuntimeError("Held-out Model controls did not preserve fail/pass separation.")
        return tuple(baseline), tuple(reference)

    @staticmethod
    def _persisted_or_generated_trace(traces, generated):
        try:
            return traces.get(generated.trace_id).trace
        except KeyError:
            return generated

    @staticmethod
    def _case(
        report: AttributionReport,
        baseline_trace_id: str,
        reference_trace_id: str,
    ) -> ExecutableModelEvidenceCase:
        task_id = next(
            item.metadata["counterfactual_trace_id"]
            for item in report.experiments
            if item.experiment_type.value == "reference_model"
        )
        del task_id  # Reference trace ID is supplied from the actual runner.
        return ExecutableModelEvidenceCase(
            task_id=baseline_trace_id.split(":tool:", 1)[-1],
            baseline_trace_id=baseline_trace_id,
            reference_trace_id=reference_trace_id,
            attribution=report,
            supported_experiments=tuple(
                item.experiment_type.value
                for item in report.experiments
                if item.supports_hypothesis
            ),
        )

    @staticmethod
    def _validate_model_attribution(report: AttributionReport) -> None:
        if (
            report.root_cause_layer != FailureLayer.MODEL
            or report.recommended_action != EvolutionAction.TRAIN_MODEL
            or not report.actionable
        ):
            raise RuntimeError("Executable Model evidence did not attribute to Model.")
        supported = [
            item.experiment_type.value
            for item in report.experiments
            if item.supports_hypothesis
        ]
        if supported != ["reference_model"]:
            raise RuntimeError("Model evidence did not rule out every external layer.")

    def _settings(
        self,
        dataset: ModelEvidenceDatasetManifest,
        signals: DatasetSignals,
    ) -> ModelEvolutionSettings:
        return ModelEvolutionSettings(
            problem_cluster=self.PROBLEM_CLUSTER,
            target_metrics=(
                MetricTarget(name="held_out_task_success", minimum_improvement=0.25),
                MetricTarget(name="safety_violation_rate", maximum_regression=0.0),
            ),
            dataset_signals=signals,
            allowed_methods=(
                TrainingMethod.AGENTIC_RL,
                TrainingMethod.DPO,
                TrainingMethod.SFT,
            ),
            budget=TrainingBudget(
                max_gpu_hours=0.0,
                max_rollouts=64,
                max_training_tokens=0,
                max_cost_usd=0.0,
            ),
            replay_environment=self.ENVIRONMENT_ID,
            safety_constraints=(
                "Do not overwrite protected documents.",
                "Do not exceed the approved Tool-call budget.",
                "Do not publish or deploy the candidate.",
            ),
            regression_suite="local-document-held-out-v1",
            evidence_dataset_uri=self.dataset_path.resolve().as_uri(),
            evidence_manifest_hash=dataset.manifest_hash,
            held_out_task_ids=dataset.held_out_task_ids,
        )

    def _backend(self, dataset: ModelEvidenceDatasetManifest) -> DryRunAgenticRLBackend:
        workspace = self.root / "agentic-rl-plan"
        workspace.mkdir(parents=True, exist_ok=True)
        return DryRunAgenticRLBackend(
            AgenticRLPlanner(),
            environment=AgenticRLEnvironmentSpec(
                environment_id=self.ENVIRONMENT_ID,
                replayable=True,
                resettable=True,
                machine_verifier=True,
                isolated=True,
                side_effect_free=True,
                max_episode_steps=6,
                dataset_ref=self.dataset_path.resolve().as_uri(),
            ),
            reward=RewardSpec(
                components=(
                    RewardComponent(
                        name="verified_task_success",
                        weight=1.0,
                        kind="reward",
                    ),
                    RewardComponent(
                        name="safety_violation",
                        weight=1.0,
                        kind="penalty",
                    ),
                    RewardComponent(
                        name="tool_call_budget",
                        weight=0.05,
                        kind="penalty",
                    ),
                )
            ),
            algorithm=RLAlgorithm.GRPO,
            workspace=str(workspace.resolve()),
        )

    def _validate_threshold_results(self, results) -> None:
        expected = (
            *(CycleStatus.MODEL_EVIDENCE_ACCUMULATED for _ in range(self.EVIDENCE_COUNT - 1)),
            CycleStatus.MODEL_CANDIDATE,
        )
        actual = tuple(item.status for item in results)
        if actual != expected:
            raise RuntimeError(
                f"Model Campaign threshold sequence differs: {[item.value for item in actual]}"
            )
        if any(item.campaign_id is not None for item in results[:-1]):
            raise RuntimeError("Model Campaign was created before the distinct-Task threshold.")

    @staticmethod
    def _campaign_artifacts(campaign):
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "model_candidate":
            raise RuntimeError("Existing Model Campaign does not contain a Candidate.")
        return (
            ModelImprovementTicket.model_validate(payload.get("ticket")),
            ModelCandidate.model_validate(payload.get("candidate")),
        )

    def _validate_campaign(
        self,
        campaign,
        ticket: ModelImprovementTicket,
        candidate: ModelCandidate,
        dataset: ModelEvidenceDatasetManifest,
    ) -> None:
        if (
            campaign.campaign_type != CampaignType.MODEL
            or campaign.state != CampaignState.CANDIDATE_READY
            or campaign.required_approvals != 2
        ):
            raise RuntimeError("Model Campaign state or risk governance is invalid.")
        if ticket.evidence_manifest_hash != dataset.manifest_hash:
            raise RuntimeError("Model Ticket is not bound to the verified dataset.")
        if ticket.evidence_trace_ids != tuple(
            item.failed.trace_id for item in dataset.examples
        ):
            raise RuntimeError("Model Ticket evidence Traces differ from the dataset.")
        if candidate.method != TrainingMethod.AGENTIC_RL:
            raise RuntimeError("Training strategy did not select Agentic RL.")
        if candidate.evidence_manifest_hash != dataset.manifest_hash:
            raise RuntimeError("Model Candidate is not bound to the verified dataset.")
        if candidate.training_executed:
            raise RuntimeError("Dry-run Model Candidate claimed that training executed.")
        if candidate.task_spec is None or candidate.task_spec.execution_enabled:
            raise RuntimeError("Agentic RL task is missing or execution was enabled.")
        if candidate.task_spec.rollout_budget != 64:
            raise RuntimeError("Agentic RL rollout budget differs from the Ticket.")

    @staticmethod
    def _validate_existing_package(
        package: ModelEvolutionPackageManifest,
        *,
        campaign,
        dataset,
        ticket,
        candidate,
        held_out_tasks,
        campaign_checkpoint,
        trace_checkpoint,
    ) -> None:
        if (
            package.campaign != campaign
            or package.dataset != dataset
            or package.ticket != ticket
            or package.candidate != candidate
            or package.held_out_tasks != held_out_tasks
            or package.campaign_checkpoint != campaign_checkpoint
            or package.trace_checkpoint != trace_checkpoint
        ):
            raise RuntimeError("Persisted Model package differs from current governed state.")

    def _verify_restart(
        self,
        *,
        campaign,
        dataset,
        package,
        campaign_checkpoint,
        trace_checkpoint,
    ) -> None:
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        traces = IdempotentJsonlTraceStore(self.trace_file)
        current = campaigns.get(campaign.campaign_id)
        if current != campaign:
            raise RuntimeError("Restart changed the persisted Model Campaign.")
        campaigns.verify_audit(campaign_checkpoint)
        traces.verify(trace_checkpoint)
        evidence = campaigns.get_model_evidence(
            base_model_id=self.BASE_MODEL_ID,
            problem_cluster=self.PROBLEM_CLUSTER,
            minimum_traces=self.EVIDENCE_COUNT,
            minimum_distinct_tasks=self.EVIDENCE_COUNT,
        )
        expected_trace_ids = tuple(item.failed.trace_id for item in dataset.examples) + (
            self._persisted_or_generated_trace(
                traces,
                self._runner(
                    index=self.FOLLOW_UP_INDEX,
                    task=self._evidence_task(self.FOLLOW_UP_INDEX, follow_up=True),
                ).baseline_trace,
            ).trace_id,
        )
        if not evidence.ready or evidence.trace_ids != expected_trace_ids:
            raise RuntimeError("Restart changed persistent distinct-Task Model evidence.")
        ModelEvidenceDatasetManager().verify(
            ModelEvidenceDatasetManager().load_file(self.dataset_path)
        )
        ModelEvolutionPackageManager().verify(
            ModelEvolutionPackageManager().load_file(self.package_path)
        )
        if package.training_executed or package.external_execution_performed:
            raise RuntimeError("Persisted package incorrectly claims external training execution.")


__all__ = [
    "ExecutableModelEvidenceCase",
    "GovernedModelEvolutionLab",
    "GovernedModelEvolutionResult",
]
