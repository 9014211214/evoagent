from __future__ import annotations

import hashlib
import json
import os
import platform
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from evoagent import __version__
from evoagent.acquisition import InitialSkillAcquisitionGate, SyntheticAcquisitionSandbox
from evoagent.benchmarks import (
    BenchmarkManifest,
    EvolutionEvaluationProtocol,
    EvolutionProtocolSpec,
    ResourceBudget,
    SyntheticFrozenEvaluator,
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
from evoagent.cycles import (
    CycleStatus,
    EvolutionCycleRequest,
    StructuredVerifierSkillBackend,
)
from evoagent.diagnosis.synthetic import SyntheticCounterfactualRunner, SyntheticFaultScenario
from evoagent.domain.models import AgentSnapshot, FailureLayer
from evoagent.integrations import SkillRecorderAdapter, SkillRecorderImportSpec
from evoagent.lab.models import (
    ReferenceEvaluationResult,
    ReferenceLabPhase,
    ReferenceLabResult,
)
from evoagent.lab.runtime import ReferenceDecisionRuntime
from evoagent.runs import (
    ReproducibleRunBundleManager,
    ReproducibleRunSpec,
    RunArtifactKind,
    RunArtifactSource,
    RunEnvironmentSpec,
    RunStatus,
)
from evoagent.skills import (
    SQLiteSkillRegistry,
    SkillEvaluationDecision,
    SkillSpec,
    SkillVersionStatus,
)
from evoagent.traces import JsonlTraceStore, TraceTrustLevel


DEFAULT_THIRD_PARTY_LOCK_HASH = (
    "38d9b1efad86df11a45c201d23299a819ec2494592e93da6b660b03dd24f33bb"
)


class ReferenceLabError(RuntimeError):
    pass


class ReferenceEvolutionLab:
    SKILL_ID = "reference_decision"
    BASE_VERSION = "0.1.0"
    RUN_ID = "reference-evolution-lab-v1"
    MODEL_ID = "synthetic/reference-model-v0"

    def __init__(
        self,
        root: str | Path,
        *,
        source_commit: str,
        source_repository: str = "https://github.com/9014211214/evoagent",
        third_party_lock_hash: str = DEFAULT_THIRD_PARTY_LOCK_HASH,
    ):
        if len(source_commit) != 40 or any(
            character not in "0123456789abcdef" for character in source_commit
        ):
            raise ValueError(
                "Reference lab source_commit must be a lowercase 40-hex Git commit."
            )
        if len(third_party_lock_hash) != 64 or any(
            character not in "0123456789abcdef"
            for character in third_party_lock_hash
        ):
            raise ValueError(
                "Reference lab third_party_lock_hash must be lowercase SHA-256 hex."
            )
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.source_commit = source_commit
        self.source_repository = source_repository
        self.third_party_lock_hash = third_party_lock_hash
        self.runtime = ReferenceDecisionRuntime()

    @property
    def skill_database(self) -> Path:
        return self.root / "skills.db"

    @property
    def campaign_database(self) -> Path:
        return self.root / "campaigns.db"

    @property
    def trace_file(self) -> Path:
        return self.root / "traces.jsonl"

    @property
    def bundle_directory(self) -> Path:
        return self.root / "run-bundle"

    def run(self) -> ReferenceLabResult:
        skills = SQLiteSkillRegistry(self.skill_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        traces = JsonlTraceStore(self.trace_file)
        governance = CampaignGovernanceService(campaigns)
        phases: list[ReferenceLabPhase] = []

        completed_at_start = self._completed_campaign(campaigns) is not None
        if self.SKILL_ID not in skills.list_skill_ids():
            self._acquire_initial_skill(skills)
            phases.append(ReferenceLabPhase.ACQUIRED)

        base_record = skills.get(self.SKILL_ID, self.BASE_VERSION)
        baseline = self.runtime.evaluate(base_record.spec)
        self._validate_baseline(baseline)
        phases.append(ReferenceLabPhase.BASELINE_EVALUATED)

        campaign = self._skill_campaign(campaigns)
        candidate_record = self._candidate_record(skills)
        if campaign is None:
            if candidate_record is not None:
                raise ReferenceLabError("Candidate exists without its governed Campaign.")
            campaign, candidate_record = self._create_candidate(
                skills=skills,
                campaigns=campaigns,
                traces=traces,
                governance=governance,
                base_skill=base_record.spec,
            )
            phases.append(ReferenceLabPhase.CANDIDATE_CREATED)
        elif candidate_record is None:
            candidate_record = self._recover_candidate_from_campaign(
                campaign=campaign,
                skills=skills,
            )

        evolved = self.runtime.evaluate(candidate_record.spec)
        self._validate_evolved(baseline, evolved)
        decision = SkillEvaluationDecision(
            skill_id=self.SKILL_ID,
            base_version=self.BASE_VERSION,
            candidate_version=candidate_record.spec.version,
            promote=True,
            base_score=baseline.score,
            candidate_score=evolved.score,
            regression_count=self._regression_count(baseline, evolved),
            reason="Frozen safe/unsafe manifest improved without regression.",
        )

        campaign = campaigns.get(campaign.campaign_id)
        if campaign.state == CampaignState.CANDIDATE_READY:
            campaign = governance.submit_evaluation(
                campaign.campaign_id,
                passed=True,
                expected_revision=campaign.revision,
                actor_id="reference-lab-evaluator",
                reason=decision.reason,
            )
            phases.append(ReferenceLabPhase.CANDIDATE_EVALUATED)

        if campaign.state == CampaignState.APPROVAL_PENDING:
            campaign = self._approve_campaign(governance, campaigns, campaign)
            phases.append(ReferenceLabPhase.CAMPAIGN_AUTHORIZED)
        if campaign.state not in {CampaignState.AUTHORIZED, CampaignState.COMPLETED}:
            raise ReferenceLabError(
                f"Reference Skill Campaign is not authorized: {campaign.state.value}"
            )

        active = skills.active(self.SKILL_ID)
        if active.spec.version == self.BASE_VERSION:
            self._validate_campaign_candidate(campaign, candidate_record.spec)
            skills.promote(
                self.SKILL_ID,
                candidate_record.spec.version,
                decision,
                expected_active_revision=skills.active_revision(self.SKILL_ID),
                actor_id="reference-lab-promoter",
            )
            phases.append(ReferenceLabPhase.SKILL_PROMOTED)
        elif active.spec.version != candidate_record.spec.version:
            raise ReferenceLabError(
                "Active Skill is neither the reference base nor the authorized candidate."
            )

        campaign = campaigns.get(campaign.campaign_id)
        if campaign.state == CampaignState.AUTHORIZED:
            campaign = campaigns.transition(
                campaign.campaign_id,
                to_state=CampaignState.COMPLETED,
                expected_revision=campaign.revision,
                actor_id="reference-lab-promoter",
                reason="Authorized candidate was explicitly promoted after frozen evaluation.",
            )
            phases.append(ReferenceLabPhase.CAMPAIGN_COMPLETED)
        if campaign.state != CampaignState.COMPLETED:
            raise ReferenceLabError("Reference Skill Campaign did not reach COMPLETED.")

        active = skills.active(self.SKILL_ID)
        evolved = self.runtime.evaluate(active.spec)
        snapshots, gain, best_round = self._evaluate_snapshots(baseline, evolved)

        skill_checkpoint = skills.checkpoint()
        campaign_checkpoint = campaigns.checkpoint()
        trace_checkpoint = traces.checkpoint()
        results_path = self._write_results(
            baseline=baseline,
            evolved=evolved,
            decision=decision,
            campaign=campaign,
            snapshots=snapshots,
            evolution_gain=gain,
            best_round=best_round,
            skill_checkpoint=skill_checkpoint.model_dump(mode="json"),
            campaign_checkpoint=campaign_checkpoint.model_dump(mode="json"),
            trace_checkpoint=trace_checkpoint.model_dump(mode="json"),
        )
        manifest_hash = self._build_or_verify_bundle(
            results_path=results_path,
            snapshots=snapshots,
        )
        phases.append(ReferenceLabPhase.EVIDENCE_BUNDLED)

        self._verify_restart(
            active_version=active.spec.version,
            campaign_id=campaign.campaign_id,
            skill_checkpoint=skill_checkpoint,
            campaign_checkpoint=campaign_checkpoint,
            trace_checkpoint=trace_checkpoint,
        )
        phases.append(ReferenceLabPhase.RESTART_VERIFIED)

        return ReferenceLabResult(
            run_id=self.RUN_ID,
            resumed=completed_at_start,
            phases=tuple(phases),
            skill_id=self.SKILL_ID,
            base_version=self.BASE_VERSION,
            active_version=active.spec.version,
            candidate_version=candidate_record.spec.version,
            campaign_id=campaign.campaign_id,
            campaign_state=campaign.state.value,
            baseline=baseline,
            evolved=evolved,
            snapshots=snapshots,
            evolution_gain=gain,
            best_round=best_round,
            skill_checkpoint=skill_checkpoint.model_dump(mode="json"),
            campaign_checkpoint=campaign_checkpoint.model_dump(mode="json"),
            trace_checkpoint=trace_checkpoint.model_dump(mode="json"),
            run_bundle_path=str(self.bundle_directory),
            run_manifest_hash=manifest_hash,
            restart_verified=True,
            external_execution_performed=False,
        )

    def _acquire_initial_skill(self, skills: SQLiteSkillRegistry) -> None:
        path, checksum = self._ensure_skill_recorder_input()
        candidate = SkillRecorderAdapter().import_candidate(
            SkillRecorderImportSpec(
                skill_json_path=str(path),
                checksum=checksum,
                consent_to_process=True,
                source_uri="synthetic://reference-lab/skill-recorder/skill.json",
                version=self.BASE_VERSION,
            )
        )
        if candidate.skill.skill_id != self.SKILL_ID:
            raise ReferenceLabError(
                "Synthetic Skill Recorder input produced an unexpected Skill ID."
            )
        InitialSkillAcquisitionGate().evaluate_and_register(
            candidate,
            sandbox=SyntheticAcquisitionSandbox(
                sandbox_id="reference-acquisition-v1"
            ),
            registry=skills,
        )

    def _ensure_skill_recorder_input(self) -> tuple[Path, str]:
        path = self.root / "skill.json"
        payload = {
            "version": 1,
            "sessionId": "reference-session-001",
            "architecture": "agent-skill",
            "name": "reference-decision",
            "description": "Handle a stable synthetic decision through an approved plan.",
            "allowedTools": [],
            "body": "Evaluate the safe synthetic case and verify the observable status.",
            "values": [],
            "plan": {
                "architecture": "agent-skill",
                "name": "reference-decision",
                "title": "Reference decision",
                "description": "Handle a stable synthetic decision through an approved plan.",
                "summary": "Evaluate and verify the stable case.",
                "generalization": "Use observable outcomes only.",
                "values": [],
                "steps": [
                    {
                        "kind": "calculation",
                        "title": "Classify stable case",
                        "text": "Classify the synthetic input as the stable safe case.",
                        "tool": "",
                    },
                    {
                        "kind": "action",
                        "title": "Return verified status",
                        "text": "Return accepted only after the observable status is verified.",
                        "tool": "",
                    },
                ],
                "allowedTools": [],
            },
            "createdAt": 1786320000000,
        }
        data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        checksum = "sha256:" + hashlib.sha256(data).hexdigest()
        if path.exists():
            if path.is_symlink() or path.read_bytes() != data:
                raise ReferenceLabError(
                    "Existing reference Skill Recorder input was modified."
                )
        else:
            self._atomic_write_bytes(path, data)
        return path, checksum

    def _create_candidate(
        self,
        *,
        skills: SQLiteSkillRegistry,
        campaigns: SQLiteCampaignRepository,
        traces: JsonlTraceStore,
        governance: CampaignGovernanceService,
        base_skill: SkillSpec,
    ):
        attempt = len(
            traces.query(
                task_type="reference-decision",
                skill_id=self.SKILL_ID,
                verifier_passed=False,
            )
        ) + 1
        trace = self.runtime.execute(
            base_skill,
            kind="unsafe",
            model_id=self.MODEL_ID,
            trace_id=f"trace:reference-unsafe:{attempt}",
        )
        service = GovernedEvolutionCycleService(
            trace_store=traces,
            skill_registry=skills,
            skill_backend=StructuredVerifierSkillBackend(),
            evidence_accumulator=PersistentModelEvidenceAccumulator(campaigns),
            campaign_governance=governance,
        )
        result = service.process(
            EvolutionCycleRequest(
                trace=trace,
                source="synthetic-reference-lab",
                trust_level=TraceTrustLevel.VERIFIED,
            ),
            counterfactual_runner=SyntheticCounterfactualRunner(
                SyntheticFaultScenario(
                    scenario_id="reference-skill-fault-v1",
                    fault_layers={FailureLayer.SKILL},
                )
            ),
        )
        if result.status != CycleStatus.SKILL_CANDIDATE:
            raise ReferenceLabError(
                f"Expected a governed Skill candidate, received {result.status.value}."
            )
        if not result.campaign_id or result.skill_candidate is None:
            raise ReferenceLabError(
                "Governed Skill result is missing Campaign or candidate data."
            )
        return campaigns.get(result.campaign_id), skills.get(
            self.SKILL_ID, result.skill_candidate.version
        )

    def _recover_candidate_from_campaign(
        self, *, campaign, skills: SQLiteSkillRegistry
    ):
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "skill_candidate" or not payload.get("candidate"):
            raise ReferenceLabError(
                "Campaign does not contain a recoverable Skill candidate."
            )
        candidate = SkillSpec.model_validate(payload["candidate"])
        skills.add_candidate(
            candidate,
            parent_version=self.BASE_VERSION,
            reason="Recovered immutable candidate from the governed Campaign.",
            actor_id="reference-lab-recovery",
        )
        return skills.get(candidate.skill_id, candidate.version)

    def _skill_campaign(self, repository: SQLiteCampaignRepository):
        campaigns = CampaignOperatorView(repository).list_campaigns(
            campaign_type=CampaignType.SKILL
        )
        target = f"skill:{self.SKILL_ID}@{self.BASE_VERSION}"
        matching = [item for item in campaigns if item.target_key == target]
        if len(matching) > 1:
            raise ReferenceLabError(
                "More than one governed Campaign owns the reference target."
            )
        return matching[0] if matching else None

    def _completed_campaign(self, repository: SQLiteCampaignRepository):
        campaign = self._skill_campaign(repository)
        return (
            campaign
            if campaign and campaign.state == CampaignState.COMPLETED
            else None
        )

    def _candidate_record(self, skills: SQLiteSkillRegistry):
        records = [
            item
            for item in skills.list_versions(self.SKILL_ID)
            if item.parent_version == self.BASE_VERSION
            and self.runtime.EVOLVED_RULE in item.spec.rules
            and item.status
            in {
                SkillVersionStatus.CANDIDATE,
                SkillVersionStatus.ACTIVE,
                SkillVersionStatus.SUPERSEDED,
            }
        ]
        if len(records) > 1:
            raise ReferenceLabError(
                "Reference lab found duplicate evolved Skill versions."
            )
        return records[0] if records else None

    @staticmethod
    def _approve_campaign(governance, repository, campaign):
        existing = {
            item.approver_id
            for item in repository.approvals(campaign.campaign_id)
        }
        index = 1
        while len(existing) < campaign.required_approvals:
            actor = f"reference-reviewer-{index}"
            index += 1
            if actor in existing:
                continue
            campaign = governance.approve(
                campaign.campaign_id,
                actor_id=actor,
                decision=ApprovalDecision.APPROVE,
                reason=(
                    "Independent reference-lab capability and regression review "
                    "passed."
                ),
                expected_revision=campaign.revision,
            )
            existing.add(actor)
        return campaign

    @staticmethod
    def _validate_campaign_candidate(campaign, candidate: SkillSpec) -> None:
        if campaign.state != CampaignState.AUTHORIZED:
            raise ReferenceLabError(
                "Only an AUTHORIZED Campaign may release a candidate for promotion."
            )
        payload = campaign.artifact_payload or {}
        if payload.get("kind") != "skill_candidate":
            raise ReferenceLabError(
                "Authorized Campaign is not a Skill candidate Campaign."
            )
        stored = SkillSpec.model_validate(payload.get("candidate"))
        if stored != candidate:
            raise ReferenceLabError(
                "Authorized Campaign candidate does not match the registry candidate."
            )

    @staticmethod
    def _regression_count(
        baseline: ReferenceEvaluationResult,
        evolved: ReferenceEvaluationResult,
    ) -> int:
        evolved_by_task = {item.task_id: item for item in evolved.cases}
        return sum(
            1
            for item in baseline.cases
            if item.passed and not evolved_by_task[item.task_id].passed
        )

    @staticmethod
    def _validate_baseline(result: ReferenceEvaluationResult) -> None:
        outcomes = {item.task_id: item.passed for item in result.cases}
        if outcomes != {"reference:safe": True, "reference:unsafe": False}:
            raise ReferenceLabError(
                f"Unexpected reference baseline outcomes: {outcomes}"
            )

    @staticmethod
    def _validate_evolved(
        baseline: ReferenceEvaluationResult,
        evolved: ReferenceEvaluationResult,
    ) -> None:
        if evolved.score <= baseline.score:
            raise ReferenceLabError(
                "Reference candidate did not improve frozen evaluation."
            )
        if any(not item.passed for item in evolved.cases):
            raise ReferenceLabError(
                "Reference candidate failed a frozen evaluation case."
            )
        if ReferenceEvolutionLab._regression_count(baseline, evolved):
            raise ReferenceLabError("Reference candidate introduced a regression.")

    def _evaluate_snapshots(self, baseline, evolved):
        manifest = self._benchmark_manifest()
        protocol = EvolutionProtocolSpec(
            protocol_id="reference-evolution-protocol-v1",
            initial_model_id=self.MODEL_ID,
            manifest=manifest,
            evolution_budget=ResourceBudget(max_task_trials=1),
            evaluation_budget=ResourceBudget(max_task_trials=2),
        )
        snapshots = (
            AgentSnapshot(
                snapshot_id="A0",
                round_index=0,
                model_id=self.MODEL_ID,
                metadata={
                    "synthetic_task_scores": {
                        item.task_id: float(item.passed)
                        for item in baseline.cases
                    }
                },
            ),
            AgentSnapshot(
                snapshot_id="A1",
                round_index=1,
                model_id=self.MODEL_ID,
                parent_snapshot_id="A0",
                metadata={
                    "synthetic_task_scores": {
                        item.task_id: float(item.passed)
                        for item in evolved.cases
                    }
                },
            ),
        )
        engine = EvolutionEvaluationProtocol()
        run = engine.evaluate_run(
            system_name="reference-evolution-lab",
            snapshots=snapshots,
            protocol=protocol,
            evaluator=SyntheticFrozenEvaluator(),
        )
        summary = engine.summarize(run)
        return snapshots, summary.evolution_gain, summary.best_round

    def _benchmark_manifest(self) -> BenchmarkManifest:
        return BenchmarkManifest(
            dataset_ref="evoagent/reference-decision",
            revision="v1",
            split="held-out",
            task_ids=("reference:safe", "reference:unsafe"),
        )

    def _write_results(self, **payload) -> Path:
        path = self.root / "reference-results.json"
        document = {
            "format_version": "evoagent-reference-lab-v1",
            "run_id": self.RUN_ID,
            "framework_version": __version__,
            "source_repository": self.source_repository,
            "source_commit": self.source_commit,
            "third_party_lock_hash": self.third_party_lock_hash,
            "external_execution_performed": False,
            **{
                key: (
                    [item.model_dump(mode="json") for item in value]
                    if key == "snapshots"
                    else value.model_dump(mode="json")
                    if hasattr(value, "model_dump")
                    else value
                )
                for key, value in payload.items()
            },
        }
        self._atomic_write_bytes(
            path,
            (json.dumps(document, indent=2, sort_keys=True) + "\n").encode(
                "utf-8"
            ),
        )
        return path

    def _build_or_verify_bundle(self, *, results_path: Path, snapshots) -> str:
        manager = ReproducibleRunBundleManager()
        if self.bundle_directory.exists():
            verification = manager.verify(self.bundle_directory)
            manifest = manager.load_manifest(self.bundle_directory)
            if not verification.verified:
                raise ReferenceLabError(
                    "Existing reference run bundle failed verification."
                )
            return manifest.manifest_hash

        spec = ReproducibleRunSpec(
            run_id=self.RUN_ID,
            created_at=datetime(2026, 8, 10, 4, 0, tzinfo=timezone.utc),
            framework_version=__version__,
            source_repository=self.source_repository,
            source_commit=self.source_commit,
            system_name="reference-evolution-lab",
            initial_model_id=self.MODEL_ID,
            snapshot_ids=tuple(item.snapshot_id for item in snapshots),
            benchmark=self._benchmark_manifest(),
            evolution_budget=ResourceBudget(max_task_trials=1),
            evaluation_budget=ResourceBudget(max_task_trials=2),
            command=(
                "python",
                "-m",
                "evoagent.lab",
                "--root",
                str(self.root),
                "--source-commit",
                self.source_commit,
                "--third-party-lock-hash",
                self.third_party_lock_hash,
            ),
            environment=RunEnvironmentSpec(
                python_version=platform.python_version(),
                platform=platform.platform(),
            ),
            status=RunStatus.DRY_RUN,
        )
        manifest = manager.build(
            spec=spec,
            artifact_sources=(
                RunArtifactSource(
                    logical_name="reference-results.json",
                    kind=RunArtifactKind.RESULTS,
                    source_path=str(results_path),
                    media_type="application/json",
                ),
            ),
            output_directory=self.bundle_directory,
        )
        verification = manager.verify(self.bundle_directory)
        if not verification.verified:
            raise ReferenceLabError("New reference run bundle failed verification.")
        return manifest.manifest_hash

    def _verify_restart(
        self,
        *,
        active_version: str,
        campaign_id: str,
        skill_checkpoint,
        campaign_checkpoint,
        trace_checkpoint,
    ) -> None:
        skills = SQLiteSkillRegistry(self.skill_database)
        campaigns = SQLiteCampaignRepository(self.campaign_database)
        traces = JsonlTraceStore(self.trace_file)
        if skills.active(self.SKILL_ID).spec.version != active_version:
            raise ReferenceLabError("Restart changed the active reference Skill.")
        if campaigns.get(campaign_id).state != CampaignState.COMPLETED:
            raise ReferenceLabError("Restart changed the reference Campaign state.")
        skills.verify_audit(skill_checkpoint)
        campaigns.verify_audit(campaign_checkpoint)
        traces.verify(trace_checkpoint)
        ReproducibleRunBundleManager().verify(self.bundle_directory)

    @staticmethod
    def _atomic_write_bytes(path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


__all__ = [
    "DEFAULT_THIRD_PARTY_LOCK_HASH",
    "ReferenceEvolutionLab",
    "ReferenceLabError",
]
