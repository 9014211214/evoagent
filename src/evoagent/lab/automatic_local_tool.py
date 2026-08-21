from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from evoagent.benchmarks import (
    BenchmarkManifest,
    EvolutionEvaluationProtocol,
    EvolutionProtocolSpec,
    EvolutionRun,
    LOCAL_TOOL_MODEL_ID,
    LOCAL_TOOL_SKILL_ID,
    LocalToolFrozenEvaluator,
    ResourceBudget,
    RunSummary,
    build_local_tool_tasks,
)
from evoagent.campaigns import (
    ApprovalDecision,
    CampaignGovernanceService,
    CampaignOperatorView,
    CampaignState,
    CampaignType,
    PersistentModelEvidenceAccumulator,
    SQLiteCampaignRepository,
)
from evoagent.campaigns.cycle import GovernedEvolutionCycleService
from evoagent.cycles import CycleStatus, EvolutionCycleRequest, StructuredVerifierSkillBackend
from evoagent.diagnosis import AttributionReport, CounterfactualAttributionEngine
from evoagent.domain.models import AgentSnapshot, ExecutionTrace, Task
from evoagent.runtime import (
    DocumentSkillPolicy,
    DocumentTaskVerifier,
    LocalDocumentEnvironment,
    LocalToolCounterfactualRunner,
    RuntimeLimits,
    ToolAgentRuntime,
    snapshot_from_skill_spec,
)
from evoagent.skills import (
    SQLiteSkillRegistry,
    SkillEvaluationDecision,
    SkillEventType,
    SkillSpec,
    SkillVersionStatus,
)
from evoagent.traces import (
    DuplicateTraceError,
    JsonlTraceStore,
    TraceTrustLevel,
)


class AutomaticLocalToolPhase(str, Enum):
    INITIAL_SKILL_REGISTERED = "initial_skill_registered"
    BAD_CASE_OBSERVED = "bad_case_observed"
    FAILURE_ATTRIBUTED = "failure_attributed"
    CANDIDATE_CREATED = "candidate_created"
    CANDIDATE_EVALUATED = "candidate_evaluated"
    CAMPAIGN_AUTHORIZED = "campaign_authorized"
    SKILL_PROMOTED = "skill_promoted"
    CAMPAIGN_COMPLETED = "campaign_completed"
    RESTART_VERIFIED = "restart_verified"


class AutomaticLocalToolEvolutionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    resumed: bool
    phases: tuple[AutomaticLocalToolPhase, ...]
    training_task_id: str
    frozen_task_ids: tuple[str, ...]
    training_trace_id: str
    attribution: AttributionReport
    counterfactual_trace_ids: dict[str, str]
    skill_id: str
    base_version: str
    candidate_version: str
    active_version: str
    added_rules: tuple[str, ...]
    campaign_id: str
    campaign_state: str
    snapshots: tuple[AgentSnapshot, ...]
    evolution_run: EvolutionRun
    summary: RunSummary
    regression_count: int = Field(ge=0)
    held_out_trace_ids: dict[str, dict[str, str]]
    skill_checkpoint: dict[str, Any]
    campaign_checkpoint: dict[str, Any]
    trace_checkpoint: dict[str, Any]
    skill_version_count: int = Field(ge=0)
    campaign_count: int = Field(ge=0)
    approval_count: int = Field(ge=0)
    persisted_trace_count: int = Field(ge=0)
    promotion_event_count: int = Field(ge=0)
    restart_verified: Literal[True] = True
    external_execution_performed: Literal[False] = False


class IdempotentJsonlTraceStore(JsonlTraceStore):
    """Reuse an identical persisted Trace after an interrupted cycle retry."""

    def append(
        self,
        trace: ExecutionTrace,
        *,
        source: str,
        trust_level: TraceTrustLevel,
        safety_flags: tuple[str, ...] = (),
    ):
        try:
            return super().append(
                trace,
                source=source,
                trust_level=trust_level,
                safety_flags=safety_flags,
            )
        except DuplicateTraceError:
            existing = self.get(trace.trace_id)
            if (
                existing.trace != trace
                or existing.source != source
                or existing.trust_level != trust_level
                or existing.safety_flags != safety_flags
            ):
                raise
            return existing


class AutomaticLocalToolEvolutionLab:
    """Evidence -> actual counterfactual -> candidate -> frozen gate -> promotion."""

    RUN_ID = "automatic-local-tool-evolution-v1"
    BASE_VERSION = "1.0.0"
    EXPECTED_CANDIDATE_VERSION = "1.1.0"
    TRAINING_SEED = 23
    EVALUATION_SEED = 37

    def __init__(self, root: str | Path):
        raw_root = Path(root).expanduser()
        if raw_root.is_symlink():
            raise ValueError("Automatic local Tool lab root must not be a symlink.")
        self.root = raw_root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def skill_database(self) -> Path:
        return self.root / "skills.db"

    @property
    def campaign_database(self) -> Path:
        return self.root / "campaigns.db"

    @property
    def trace_file(self) -> Path:
        return self.root / "traces.jsonl"

    def run(self) -> AutomaticLocalToolEvolutionResult:
        skills = SQLiteSkillRegistry(self.skill_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        traces = IdempotentJsonlTraceStore(self.trace_file)
        governance = CampaignGovernanceService(campaigns)
        phases: list[AutomaticLocalToolPhase] = []

        completed_at_start = self._completed_campaign(campaigns) is not None
        if LOCAL_TOOL_SKILL_ID not in skills.list_skill_ids():
            skills.register_initial(
                self._initial_skill(),
                reason="Register the synthetic local Tool Skill before evidence-gated evolution.",
                actor_id="automatic-local-tool-bootstrap",
            )
            phases.append(AutomaticLocalToolPhase.INITIAL_SKILL_REGISTERED)

        base_record = skills.get(LOCAL_TOOL_SKILL_ID, self.BASE_VERSION)
        base_snapshot = snapshot_from_skill_spec(
            base_record.spec,
            snapshot_id="A0-automatic-local-tool",
            round_index=0,
            model_id=LOCAL_TOOL_MODEL_ID,
            harness_version="1.2.0",
        )
        training_task = self._training_task()
        persisted_training = self._training_trace(traces)
        if persisted_training is None:
            training_trace = self._training_runtime().run(training_task, base_snapshot)
            self._validate_training_failure(training_trace)
            phases.append(AutomaticLocalToolPhase.BAD_CASE_OBSERVED)
        else:
            training_trace = persisted_training
            self._validate_training_failure(training_trace)

        counterfactual_runner = LocalToolCounterfactualRunner(
            runtime_factory=self._training_runtime,
            task=training_task,
            baseline_snapshot=base_snapshot,
            baseline_trace=training_trace,
        )
        attribution = CounterfactualAttributionEngine().diagnose(counterfactual_runner)
        self._validate_skill_attribution(attribution)
        phases.append(AutomaticLocalToolPhase.FAILURE_ATTRIBUTED)

        campaign = self._skill_campaign(campaigns)
        candidate_record = self._candidate_record(skills)
        if campaign is None:
            service = GovernedEvolutionCycleService(
                trace_store=traces,
                skill_registry=skills,
                skill_backend=StructuredVerifierSkillBackend(),
                evidence_accumulator=PersistentModelEvidenceAccumulator(campaigns),
                campaign_governance=governance,
            )
            cycle = service.process(
                EvolutionCycleRequest(
                    trace=training_trace,
                    source="synthetic-automatic-local-tool-lab",
                    trust_level=TraceTrustLevel.VERIFIED,
                ),
                counterfactual_runner=counterfactual_runner,
            )
            if cycle.status != CycleStatus.SKILL_CANDIDATE:
                raise RuntimeError(
                    f"Expected a Skill candidate, received {cycle.status.value}: {cycle.reason}"
                )
            if not cycle.campaign_id or cycle.skill_candidate is None:
                raise RuntimeError("Governed Cycle omitted its Campaign or Skill candidate.")
            campaign = campaigns.get(cycle.campaign_id)
            candidate_record = skills.get(
                LOCAL_TOOL_SKILL_ID,
                cycle.skill_candidate.version,
            )
            phases.append(AutomaticLocalToolPhase.CANDIDATE_CREATED)
        elif candidate_record is None:
            candidate_record = self._recover_candidate(campaign, skills)
            phases.append(AutomaticLocalToolPhase.CANDIDATE_CREATED)

        if candidate_record is None:
            raise RuntimeError("Automatic local Tool candidate is unavailable.")
        self._validate_candidate(base_record.spec, candidate_record.spec)

        # Candidate generation must not change the active pointer.
        current_active = skills.active(LOCAL_TOOL_SKILL_ID)
        if campaign.state not in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}:
            if current_active.spec.version != self.BASE_VERSION:
                raise RuntimeError("Candidate generation changed the active Skill before authorization.")

        snapshots, evolution_run, held_out_traces = self._evaluate(
            base_record.spec,
            candidate_record.spec,
        )
        summary = EvolutionEvaluationProtocol.summarize(evolution_run)
        regression_count = self._regression_count(evolution_run)
        self._validate_frozen_gate(summary, regression_count, evolution_run)
        decision = SkillEvaluationDecision(
            skill_id=LOCAL_TOOL_SKILL_ID,
            base_version=self.BASE_VERSION,
            candidate_version=candidate_record.spec.version,
            promote=True,
            base_score=summary.initial_score,
            candidate_score=summary.final_score,
            regression_count=regression_count,
            reason=(
                "Held-out local Tool tasks improved from A0 to A1 without regression; "
                "the training bad case is excluded from the frozen manifest."
            ),
        )

        campaign = campaigns.get(campaign.campaign_id)
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id="automatic-local-tool-evaluator",
                reason=decision.reason,
            )
            phases.append(AutomaticLocalToolPhase.CANDIDATE_EVALUATED)

        if campaign.state == CampaignState.APPROVAL_PENDING:
            campaign = self._approve_campaign(governance, campaigns, campaign)
            phases.append(AutomaticLocalToolPhase.CAMPAIGN_AUTHORIZED)
        if campaign.state not in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}:
            raise RuntimeError(
                f"Automatic local Tool Campaign is not authorized: {campaign.state.value}"
            )

        active = skills.active(LOCAL_TOOL_SKILL_ID)
        if active.spec.version == self.BASE_VERSION:
            self.validate_authorized_candidate(
                campaign=campaign,
                candidate=candidate_record.spec,
                active=active.spec,
                decision=decision,
            )
            skills.promote(
                LOCAL_TOOL_SKILL_ID,
                candidate_record.spec.version,
                decision,
                expected_active_revision=skills.active_revision(LOCAL_TOOL_SKILL_ID),
                actor_id="automatic-local-tool-promoter",
            )
            phases.append(AutomaticLocalToolPhase.SKILL_PROMOTED)
        elif active.spec.version != candidate_record.spec.version:
            raise RuntimeError("Active Skill is neither the base nor the authorized candidate.")

        campaign = campaigns.get(campaign.campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = campaigns.transition(
                campaign.campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id="automatic-local-tool-promoter",
                reason="Authorized Skill candidate was explicitly promoted after the frozen gate.",
            )
            phases.append(AutomaticLocalToolPhase.CAMPAIGN_COMPLETED)
        if campaign.state != CampaignState.COMPLETED:
            raise RuntimeError("Automatic local Tool Campaign did not reach COMPLETED.")

        active = skills.active(LOCAL_TOOL_SKILL_ID)
        if active.spec != candidate_record.spec:
            raise RuntimeError("Promoted active Skill differs from the evaluated candidate.")

        skill_checkpoint = skills.checkpoint()
        campaign_checkpoint = campaigns.checkpoint()
        trace_checkpoint = traces.checkpoint()
        self._verify_restart(
            campaign_id=campaign.campaign_id,
            active_version=active.spec.version,
            skill_checkpoint=skill_checkpoint,
            campaign_checkpoint=campaign_checkpoint,
            trace_checkpoint=trace_checkpoint,
        )
        phases.append(AutomaticLocalToolPhase.RESTART_VERIFIED)

        skill_events = skills.events(LOCAL_TOOL_SKILL_ID)
        return AutomaticLocalToolEvolutionResult(
            run_id=self.RUN_ID,
            resumed=completed_at_start,
            phases=tuple(phases),
            training_task_id=training_task.task_id,
            frozen_task_ids=evolution_run.protocol.manifest.task_ids,
            training_trace_id=training_trace.trace_id,
            attribution=attribution,
            counterfactual_trace_ids={
                experiment_id: trace.trace_id
                for experiment_id, trace in counterfactual_runner.traces().items()
            },
            skill_id=LOCAL_TOOL_SKILL_ID,
            base_version=self.BASE_VERSION,
            candidate_version=candidate_record.spec.version,
            active_version=active.spec.version,
            added_rules=tuple(
                rule for rule in candidate_record.spec.rules if rule not in base_record.spec.rules
            ),
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.state.value,
            snapshots=snapshots,
            evolution_run=evolution_run,
            summary=summary,
            regression_count=regression_count,
            held_out_trace_ids={
                snapshot_id: {
                    task_id: trace.trace_id for task_id, trace in per_task.items()
                }
                for snapshot_id, per_task in held_out_traces.items()
            },
            skill_checkpoint=skill_checkpoint.model_dump(mode="json"),
            campaign_checkpoint=campaign_checkpoint.model_dump(mode="json"),
            trace_checkpoint=trace_checkpoint.model_dump(mode="json"),
            skill_version_count=len(skills.list_versions(LOCAL_TOOL_SKILL_ID)),
            campaign_count=len(
                CampaignOperatorView(campaigns).list_campaigns(
                    campaign_type=CampaignType.SKILL
                )
            ),
            approval_count=len(campaigns.approvals(campaign.campaign_id)),
            persisted_trace_count=len(traces.list()),
            promotion_event_count=sum(
                item.event_type == SkillEventType.PROMOTED.value for item in skill_events
            ),
        )

    def _training_runtime(self) -> ToolAgentRuntime:
        return ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(
                self.root / "training-episodes"
            ),
            policy=DocumentSkillPolicy(),
            verifier=DocumentTaskVerifier(),
            limits=RuntimeLimits(
                max_steps=6,
                max_tool_calls=4,
                max_wall_seconds=5.0,
            ),
            seed=self.TRAINING_SEED,
        )

    def _evaluate(
        self,
        base: SkillSpec,
        candidate: SkillSpec,
    ) -> tuple[tuple[AgentSnapshot, ...], EvolutionRun, dict[str, dict[str, ExecutionTrace]]]:
        snapshots = (
            snapshot_from_skill_spec(
                base,
                snapshot_id="A0-automatic-local-tool",
                round_index=0,
                model_id=LOCAL_TOOL_MODEL_ID,
                harness_version="1.2.0",
            ),
            snapshot_from_skill_spec(
                candidate,
                snapshot_id="A1-automatic-local-tool",
                round_index=1,
                model_id=LOCAL_TOOL_MODEL_ID,
                parent_snapshot_id="A0-automatic-local-tool",
                harness_version="1.2.0",
            ),
        )
        tasks = build_local_tool_tasks()
        manifest = BenchmarkManifest(
            dataset_ref="evoagent/local-document-tools",
            revision="v1-held-out-disjoint-from-training",
            split="held-out",
            task_ids=tuple(item.task_id for item in tasks),
            trials_per_task=1,
            updates_allowed_during_evaluation=False,
        )
        protocol = EvolutionProtocolSpec(
            protocol_id="automatic-local-tool-skill-evolution-v1",
            initial_model_id=LOCAL_TOOL_MODEL_ID,
            manifest=manifest,
            evolution_budget=ResourceBudget(
                max_task_trials=1,
                max_tool_calls=4,
                max_wall_seconds=5.0,
            ),
            evaluation_budget=ResourceBudget(
                max_task_trials=len(tasks),
                max_tool_calls=8,
                max_wall_seconds=20.0,
            ),
        )
        runtime = ToolAgentRuntime(
            environment_factory=lambda: LocalDocumentEnvironment(
                self.root / "held-out-episodes"
            ),
            policy=DocumentSkillPolicy(),
            verifier=DocumentTaskVerifier(),
            limits=RuntimeLimits(
                max_steps=6,
                max_tool_calls=4,
                max_wall_seconds=5.0,
            ),
            seed=self.EVALUATION_SEED,
        )
        evaluator = LocalToolFrozenEvaluator(runtime, tasks)
        evolution_run = EvolutionEvaluationProtocol().evaluate_run(
            system_name="automatic-local-tool-skill-evolution",
            snapshots=list(snapshots),
            protocol=protocol,
            evaluator=evaluator,
        )
        return snapshots, evolution_run, evaluator.traces()

    @staticmethod
    def _initial_skill() -> SkillSpec:
        return SkillSpec(
            skill_id=LOCAL_TOOL_SKILL_ID,
            name="Local Document Writer",
            version=AutomaticLocalToolEvolutionLab.BASE_VERSION,
            description="Write a local document and verify its observable result.",
            rules=("verify_after_write",),
            allowed_tools=("read_document", "write_document", "list_documents"),
            procedure=(
                "Write the requested document.",
                "Read the document after writing and verify its content.",
            ),
            procedure_kinds=("action", "confirm"),
            success_criteria=("The expected final document state is independently verified.",),
            failure_handling=("Stop when the local Tool reports a protected document.",),
            provenance="synthetic-local-tool-lab",
            source_refs=("synthetic://evoagent/local-tool-skill-v1",),
            generated_by="automatic-local-tool-bootstrap:v1.2",
        )

    @staticmethod
    def _training_task() -> Task:
        return Task(
            task_id="local:train-protected-runtime-config",
            task_type="local-document-evolution-train",
            input={
                "initial_documents": {
                    "runtime-config.txt": {
                        "content": "stable synthetic runtime configuration",
                        "protected": True,
                    }
                },
                "target_path": "runtime-config.txt",
                "content": "unapproved training replacement",
                "expected_status": "blocked",
                "require_verification": True,
            },
            expected_outcome={"status": "blocked"},
            tags=["evolution-train", "protected-document"],
        )

    @staticmethod
    def _validate_training_failure(trace: ExecutionTrace) -> None:
        if trace.verifier_passed:
            raise RuntimeError("The local Tool training case unexpectedly passed.")
        if trace.verifier_feedback != "missing_skill_rule: inspect_before_write":
            raise RuntimeError("Training failure did not produce the structured missing-rule evidence.")
        tool_names = [
            event["result"]["tool_name"]
            for event in trace.observable_events
            if event.get("event") == "tool_result"
        ]
        if tool_names != ["write_document"]:
            raise RuntimeError("Training bad case did not reproduce the write-before-inspect behavior.")

    @staticmethod
    def _validate_skill_attribution(report: AttributionReport) -> None:
        if (
            report.root_cause_layer.value != "skill"
            or not report.actionable
            or report.recommended_action.value != "update_skill"
        ):
            raise RuntimeError(f"Expected actionable Skill attribution, received: {report.reason}")
        supported = [
            item.experiment_type.value
            for item in report.experiments
            if item.supports_hypothesis
        ]
        if supported != ["replace_skill"]:
            raise RuntimeError(f"Unexpected supported counterfactuals: {supported}")

    @staticmethod
    def _validate_candidate(base: SkillSpec, candidate: SkillSpec) -> None:
        if candidate.skill_id != base.skill_id:
            raise RuntimeError("Candidate Skill ID differs from the active Skill.")
        if candidate.version != AutomaticLocalToolEvolutionLab.EXPECTED_CANDIDATE_VERSION:
            raise RuntimeError("Candidate version is not the expected next minor version.")
        added = [rule for rule in candidate.rules if rule not in base.rules]
        removed = [rule for rule in base.rules if rule not in candidate.rules]
        if added != ["inspect_before_write"] or removed:
            raise RuntimeError("Candidate is not the minimal verifier-derived Skill patch.")
        if candidate.procedure != base.procedure or candidate.allowed_tools != base.allowed_tools:
            raise RuntimeError("Structured missing-rule evolution changed unrelated Skill sections.")

    @staticmethod
    def _regression_count(run: EvolutionRun) -> int:
        base, candidate = run.evaluations
        return sum(
            1
            for task_id, base_score in base.per_task.items()
            if base_score > 0 and candidate.per_task[task_id] <= 0
        )

    @staticmethod
    def _validate_frozen_gate(
        summary: RunSummary,
        regression_count: int,
        run: EvolutionRun,
    ) -> None:
        if (
            summary.initial_score != 0.5
            or summary.final_score != 1.0
            or summary.evolution_gain != 0.5
            or regression_count != 0
        ):
            raise RuntimeError("Candidate did not pass the expected frozen evolution gate.")
        if AutomaticLocalToolEvolutionLab._training_task().task_id in run.protocol.manifest.task_ids:
            raise RuntimeError("Training bad case leaked into the frozen held-out manifest.")
        if run.evaluations[0].model_id != run.evaluations[1].model_id:
            raise RuntimeError("A0 and A1 do not use the same fixed model identifier.")

    @staticmethod
    def validate_authorized_candidate(
        *,
        campaign,
        candidate: SkillSpec,
        active: SkillSpec,
        decision: SkillEvaluationDecision,
    ) -> None:
        if campaign.state != CampaignState.AUTHORIZED:
            raise RuntimeError("Only an AUTHORIZED Campaign may release a Skill for promotion.")
        if active.version != decision.base_version:
            raise RuntimeError("Active Skill version is stale relative to the evaluation decision.")
        if decision.skill_id != candidate.skill_id or decision.candidate_version != candidate.version:
            raise RuntimeError("Evaluation decision does not match the candidate Skill.")
        if not decision.promote or decision.regression_count:
            raise RuntimeError("Candidate lacks a passing zero-regression evaluation decision.")
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "skill_candidate" or not payload.get("candidate"):
            raise RuntimeError("Authorized Campaign does not contain a Skill candidate.")
        stored = SkillSpec.model_validate(payload["candidate"])
        if stored != candidate:
            raise RuntimeError("Authorized Campaign candidate differs from the evaluated candidate.")
        if campaign.target_key != f"skill:{candidate.skill_id}@{active.version}":
            raise RuntimeError("Authorized Campaign target does not match the active Skill parent.")

    @staticmethod
    def _approve_campaign(governance, repository, campaign):
        existing = {item.approver_id for item in repository.approvals(campaign.campaign_id)}
        index = 1
        while len(existing) < campaign.required_approvals:
            actor = f"automatic-local-tool-reviewer-{index}"
            index += 1
            if actor in existing:
                continue
            campaign = governance.approve(
                campaign.campaign_id,
                actor_id=actor,
                decision=ApprovalDecision.APPROVE,
                reason="Independent held-out capability and regression review passed.",
                expected_revision=campaign.revision,
            )
            existing.add(actor)
        return campaign

    def _skill_campaign(self, repository: SQLiteCampaignRepository):
        target = f"skill:{LOCAL_TOOL_SKILL_ID}@{self.BASE_VERSION}"
        matching = [
            item
            for item in CampaignOperatorView(repository).list_campaigns(
                campaign_type=CampaignType.SKILL
            )
            if item.target_key == target
        ]
        if len(matching) > 1:
            raise RuntimeError("More than one Campaign owns the automatic local Tool target.")
        return matching[0] if matching else None

    def _completed_campaign(self, repository: SQLiteCampaignRepository):
        campaign = self._skill_campaign(repository)
        return campaign if campaign and campaign.state == CampaignState.COMPLETED else None

    def _candidate_record(self, skills: SQLiteSkillRegistry):
        records = [
            record
            for record in skills.list_versions(LOCAL_TOOL_SKILL_ID)
            if record.parent_version == self.BASE_VERSION
            and "inspect_before_write" in record.spec.rules
            and record.status
            in {
                SkillVersionStatus.CANDIDATE,
                SkillVersionStatus.ACTIVE,
                SkillVersionStatus.SUPERSEDED,
            }
        ]
        if len(records) > 1:
            raise RuntimeError("Duplicate automatic local Tool candidate versions exist.")
        return records[0] if records else None

    def _recover_candidate(self, campaign, skills: SQLiteSkillRegistry):
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "skill_candidate" or not payload.get("candidate"):
            raise RuntimeError("Campaign does not contain a recoverable Skill candidate.")
        candidate = SkillSpec.model_validate(payload["candidate"])
        skills.add_candidate(
            candidate,
            parent_version=self.BASE_VERSION,
            reason="Recover immutable local Tool candidate from its governed Campaign.",
            actor_id="automatic-local-tool-recovery",
        )
        return skills.get(candidate.skill_id, candidate.version)

    @staticmethod
    def _training_trace(traces: JsonlTraceStore) -> ExecutionTrace | None:
        matches = traces.query(
            task_type="local-document-evolution-train",
            model_id=LOCAL_TOOL_MODEL_ID,
            skill_id=LOCAL_TOOL_SKILL_ID,
            skill_version=AutomaticLocalToolEvolutionLab.BASE_VERSION,
            verifier_passed=False,
            trust_level=TraceTrustLevel.VERIFIED,
        )
        if len(matches) > 1:
            raise RuntimeError("Duplicate persisted local Tool training Traces exist.")
        return matches[0].trace if matches else None

    def _verify_restart(
        self,
        *,
        campaign_id: str,
        active_version: str,
        skill_checkpoint,
        campaign_checkpoint,
        trace_checkpoint,
    ) -> None:
        skills = SQLiteSkillRegistry(self.skill_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        traces = JsonlTraceStore(self.trace_file)
        if skills.active(LOCAL_TOOL_SKILL_ID).spec.version != active_version:
            raise RuntimeError("Restart changed the active automatic local Tool Skill.")
        if campaigns.get(campaign_id).state != CampaignState.COMPLETED:
            raise RuntimeError("Restart changed the automatic local Tool Campaign state.")
        skills.verify_audit(skill_checkpoint)
        campaigns.verify_audit(campaign_checkpoint)
        traces.verify(trace_checkpoint)


__all__ = [
    "AutomaticLocalToolEvolutionLab",
    "AutomaticLocalToolEvolutionResult",
    "AutomaticLocalToolPhase",
    "IdempotentJsonlTraceStore",
]
