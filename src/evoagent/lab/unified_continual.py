from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evoagent.benchmarks.models import ResourceBudget
from evoagent.continual import (
    BoundedObservablePolicyOptimizer,
    ContinualComponent,
    ContinualEvaluationReport,
    ContinualPromotionDecision,
    ContinualLoopAction,
    ContinualTaskRole,
    SQLiteContinualSnapshotRegistry,
    UnifiedAgentSnapshot,
    UnifiedAttributionReport,
    UnifiedContinualEvaluator,
    UnifiedCounterfactualRunner,
    append_verified_memory,
    build_action_policy,
    build_gate_policy,
    build_loop_policy,
    build_memory_record,
    build_memory_snapshot,
    build_policy_optimization_config,
    build_router_policy,
    build_router_rule,
    build_task_manifest,
    build_task_spec,
    build_unified_snapshot,
    decide_promotion,
    decide_loop_action,
)
from evoagent.domain.models import Task
from evoagent.model_registry.models import canonical_sha256
from evoagent.runtime import RuntimeLimits
from evoagent.skills import SkillSpec


MODEL_ID = "synthetic/frozen-unified-document-agent-v1"
LINEAGE_ID = "unified-continual-reference"
WRITER_ID = "document_writer"
INSPECTOR_ID = "document_inspector"


class UnifiedContinualLabResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    resumed: bool
    optimizer_invoked: bool
    snapshot_ids: tuple[str, ...]
    snapshot_hashes: tuple[str, ...]
    overall_scores: tuple[float, ...]
    final_role_scores: dict[ContinualTaskRole, float]
    changed_components: tuple[ContinualComponent, ...]
    attribution_components: tuple[ContinualComponent, ...]
    decision_hashes: tuple[str, ...]
    loop_actions: tuple[ContinualLoopAction, ...]
    loop_decision_hashes: tuple[str, ...]
    memory_record_count: int
    policy_parameter_delta_l2: float
    final_regression_count: int
    final_forgetting_rate: float
    final_safety_violation_count: int
    registry_revision: int
    registry_event_count: int
    result_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    unified_runtime_executed: Literal[True] = True
    skill_router_memory_policy_shared_one_runtime: Literal[True] = True
    numeric_agent_policy_updated: Literal[True] = True
    external_model_called: Literal[False] = False
    foundation_model_weights_changed: Literal[False] = False
    external_benchmark_claimed: Literal[False] = False

    @model_validator(mode="after")
    def validate_result_hash(self):
        payload = self.model_dump(
            mode="json",
            exclude={"result_hash", "resumed", "optimizer_invoked"},
        )
        if self.result_hash != canonical_sha256(payload):
            raise ValueError("Unified continual Lab result hash mismatch.")
        return self


class UnifiedContinualEvolutionLab:
    """Zero-cost A0→A4 reference loop over one real Tool-Agent runtime."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime_hash = canonical_sha256("UnifiedDocumentAgentRuntime:v1")
        self.tool_hash = canonical_sha256(
            ("read_document", "write_document", "list_documents")
        )
        self.verifier_hash = canonical_sha256("ContinualDocumentVerifier:v1")

    def run(self) -> UnifiedContinualLabResult:
        snapshots: list[UnifiedAgentSnapshot] = []
        reports: list[ContinualEvaluationReport] = []
        decisions: list[ContinualPromotionDecision] = []
        attributions: list[UnifiedAttributionReport] = []
        registry = SQLiteContinualSnapshotRegistry(self.root / "unified-registry.db")
        result_path = self.root / "unified-result.json"
        if result_path.exists():
            if result_path.is_symlink() or not result_path.is_file():
                raise RuntimeError("Unified Lab result must be a regular file.")
            result = UnifiedContinualLabResult.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
            registry.verify_state(LINEAGE_ID)
            head = registry.head(LINEAGE_ID)
            active = registry.record(head.active_snapshot_id)
            if (
                head.revision != result.registry_revision
                or active.snapshot.snapshot_hash != result.snapshot_hashes[-1]
                or len(registry.events()) != result.registry_event_count
            ):
                raise RuntimeError("Persistent unified Lab state differs from its result.")
            return UnifiedContinualLabResult(
                **result.model_dump(
                    exclude={"resumed", "optimizer_invoked", "result_hash"}
                ),
                resumed=True,
                optimizer_invoked=False,
                result_hash=result.result_hash,
            )
        evaluator = UnifiedContinualEvaluator(self.root / "evaluation")
        manifest = self._evaluation_manifest()
        gate = build_gate_policy(
            minimum_target_gain=0.01,
            maximum_retention_drop=0.0,
            maximum_regressions=0,
            # A0 intentionally contains one known safety failure. Intermediate
            # candidates may improve other components, but the final STOP gate
            # below still requires the safety count to reach zero.
            require_zero_safety_violations=False,
            maximum_tool_call_growth=2.0,
        )

        a0 = self._initial_snapshot()
        registry.register_initial(a0, actor_id="registry-bootstrap")
        snapshots.append(a0)
        r0 = evaluator.evaluate(a0, manifest, report_id="unified-report-A0")
        reports.append(r0)

        a1 = self._skill_candidate(a0)
        skill_attr = UnifiedCounterfactualRunner(
            self.root / "counterfactual-skill",
            seed=manifest.seed,
            limits=manifest.runtime_limits,
        ).run(
            manifest.tasks[0].task,
            a0,
            {ContinualComponent.SKILL: a1},
            report_id="unified-attribution-skill",
        )
        self._require_attribution(skill_attr, ContinualComponent.SKILL)
        attributions.append(skill_attr)
        r1, d1 = self._evaluate_and_activate(
            registry,
            evaluator,
            manifest,
            gate,
            a0,
            a1,
            r0,
            target_roles=(ContinualTaskRole.RETENTION, ContinualTaskRole.COMPOSITION),
        )
        snapshots.append(a1)
        reports.append(r1)
        decisions.append(d1)

        a2 = self._memory_candidate(a1)
        r2, d2 = self._evaluate_and_activate(
            registry,
            evaluator,
            manifest,
            gate,
            a1,
            a2,
            r1,
            target_roles=(ContinualTaskRole.TRANSFER,),
        )
        snapshots.append(a2)
        reports.append(r2)
        decisions.append(d2)

        a3 = self._router_candidate(a2)
        router_task = next(
            item.task for item in manifest.tasks if item.task.task_id == "heldout:router-shift"
        )
        router_attr = UnifiedCounterfactualRunner(
            self.root / "counterfactual-router",
            seed=manifest.seed,
            limits=manifest.runtime_limits,
        ).run(
            router_task,
            a2,
            {ContinualComponent.ROUTER: a3},
            report_id="unified-attribution-router",
        )
        self._require_attribution(router_attr, ContinualComponent.ROUTER)
        attributions.append(router_attr)
        r3, d3 = self._evaluate_and_activate(
            registry,
            evaluator,
            manifest,
            gate,
            a2,
            a3,
            r2,
            target_roles=(ContinualTaskRole.TRANSFER,),
        )
        snapshots.append(a3)
        reports.append(r3)
        decisions.append(d3)

        optimization = BoundedObservablePolicyOptimizer(
            self.root / "policy-optimization"
        ).train(
            a3,
            (self._training_adversarial_task(),),
            config=build_policy_optimization_config(
                iterations=20,
                group_size=16,
                maximum_rollouts=320,
                maximum_episode_steps=2560,
                seed=29,
            ),
            result_id="unified-policy-optimization-A4",
        )
        a4 = build_unified_snapshot(
            lineage_id=LINEAGE_ID,
            snapshot_id="A4-unified-policy",
            round_index=4,
            model_id=MODEL_ID,
            skills=a3.skills,
            router=a3.router,
            memory=a3.memory,
            action_policy=optimization.candidate_policy,
            runtime_hash=self.runtime_hash,
            tool_contract_hash=self.tool_hash,
            verifier_hash=self.verifier_hash,
            creator_id="policy-candidate-planner",
            parent=a3,
            changed_component=ContinualComponent.POLICY,
            evidence_hashes=(optimization.result_hash,),
        )
        adversarial_task = next(
            item.task for item in manifest.tasks if item.role == ContinualTaskRole.ADVERSARIAL
        )
        policy_attr = UnifiedCounterfactualRunner(
            self.root / "counterfactual-policy",
            seed=manifest.seed,
            limits=manifest.runtime_limits,
        ).run(
            adversarial_task,
            a3,
            {ContinualComponent.POLICY: a4},
            report_id="unified-attribution-policy",
        )
        self._require_attribution(policy_attr, ContinualComponent.POLICY)
        attributions.append(policy_attr)
        r4, d4 = self._evaluate_and_activate(
            registry,
            evaluator,
            manifest,
            gate,
            a3,
            a4,
            r3,
            target_roles=(ContinualTaskRole.ADVERSARIAL,),
        )
        snapshots.append(a4)
        reports.append(r4)
        decisions.append(d4)

        registry.verify_state(LINEAGE_ID)
        loop_policy = build_loop_policy(
            target_score=1.0,
            maximum_rounds=4,
            maximum_non_improving_rounds=2,
            maximum_forgetting_rate=0.0,
            require_zero_safety_violations=True,
        )
        loop_decisions = tuple(
            decide_loop_action(
                report,
                policy=loop_policy,
                completed_rounds=index,
                consecutive_non_improving_rounds=0,
                decision_id=f"unified-loop-decision-{index}",
            )
            for index, report in enumerate(reports)
        )
        if tuple(item.action for item in loop_decisions) != (
            ContinualLoopAction.CONTINUE,
            ContinualLoopAction.CONTINUE,
            ContinualLoopAction.CONTINUE,
            ContinualLoopAction.CONTINUE,
            ContinualLoopAction.STOP_SUCCESS,
        ):
            raise RuntimeError("Unified continual loop decisions drifted.")
        if (
            r4.overall_score != 1.0
            or any(score != 1.0 for score in r4.role_scores.values())
            or r4.regression_count
            or r4.forgetting_rate
            or r4.safety_violation_count
        ):
            raise RuntimeError("Unified continual reference loop did not reach its STOP gate.")
        if tuple(item.overall_score for item in reports) != (0.0, 0.4, 0.6, 0.8, 1.0):
            raise RuntimeError("Unified continual score sequence drifted.")
        head = registry.head(LINEAGE_ID)
        payload = {
            "resumed": False,
            "optimizer_invoked": True,
            "snapshot_ids": tuple(item.snapshot_id for item in snapshots),
            "snapshot_hashes": tuple(item.snapshot_hash for item in snapshots),
            "overall_scores": tuple(item.overall_score for item in reports),
            "final_role_scores": r4.role_scores,
            "changed_components": tuple(item.changed_component for item in snapshots[1:]),
            "attribution_components": tuple(item.supported_component for item in attributions),
            "decision_hashes": tuple(item.decision_hash for item in decisions),
            "loop_actions": tuple(item.action for item in loop_decisions),
            "loop_decision_hashes": tuple(
                item.decision_hash for item in loop_decisions
            ),
            "memory_record_count": len(a4.memory.records),
            "policy_parameter_delta_l2": optimization.parameter_delta_l2,
            "final_regression_count": r4.regression_count,
            "final_forgetting_rate": r4.forgetting_rate,
            "final_safety_violation_count": r4.safety_violation_count,
            "registry_revision": head.revision,
            "registry_event_count": len(registry.events()),
        }
        provisional = UnifiedContinualLabResult.model_construct(
            **payload,
            result_hash="0" * 64,
        )
        hash_payload = provisional.model_dump(
            mode="json",
            exclude={"result_hash", "resumed", "optimizer_invoked"},
        )
        result = UnifiedContinualLabResult(
            **payload,
            result_hash=canonical_sha256(hash_payload),
        )
        result_path.write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return result

    def _initial_snapshot(self) -> UnifiedAgentSnapshot:
        writer = SkillSpec(
            skill_id=WRITER_ID,
            name="Document writer",
            version="1.0.0",
            description="Write a bounded local document.",
            rules=(),
            generated_by="reference-fixture",
        )
        inspector = SkillSpec(
            skill_id=INSPECTOR_ID,
            name="Document inspector",
            version="1.0.0",
            description="Inspect before changing an uncertain document.",
            rules=("inspect_before_write",),
            generated_by="reference-fixture",
        )
        rules = (
            build_router_rule(
                "route-core",
                task_type="continual-document",
                required_tags=("route:core",),
                skill_ids=(WRITER_ID,),
                priority=20,
            ),
            build_router_rule(
                "route-composition",
                task_type="continual-document",
                required_tags=("route:composition",),
                skill_ids=(WRITER_ID, INSPECTOR_ID),
                priority=20,
            ),
            build_router_rule(
                "route-shift",
                task_type="continual-document",
                required_tags=("route:shift",),
                skill_ids=(INSPECTOR_ID,),
                priority=20,
            ),
            build_router_rule(
                "route-adversarial",
                task_type="continual-document",
                required_tags=("route:adversarial",),
                skill_ids=(INSPECTOR_ID,),
                priority=20,
            ),
        )
        router = build_router_policy(
            "unified-router",
            version=0,
            rules=rules,
            default_skill_ids=(INSPECTOR_ID,),
        )
        memory = build_memory_snapshot("unified-memory", version=0, max_records=16)
        action_policy = build_action_policy(
            "unified-action-policy",
            version=0,
            iteration=0,
            state_keys=("core", "transfer", "composition", "route", "adversarial"),
            logits=((0.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, 1.0), (0.0, 4.0)),
        )
        return build_unified_snapshot(
            lineage_id=LINEAGE_ID,
            snapshot_id="A0-unified",
            round_index=0,
            model_id=MODEL_ID,
            skills=(writer, inspector),
            router=router,
            memory=memory,
            action_policy=action_policy,
            runtime_hash=self.runtime_hash,
            tool_contract_hash=self.tool_hash,
            verifier_hash=self.verifier_hash,
            creator_id="reference-bootstrap",
        )

    def _skill_candidate(self, parent: UnifiedAgentSnapshot) -> UnifiedAgentSnapshot:
        writer, inspector = parent.skills
        evolved = writer.model_copy(
            update={
                "version": "1.1.0",
                "rules": ("verify_after_write",),
                "generated_by": "verified-skill-candidate",
            }
        )
        evidence = canonical_sha256("missing verified post-write observation")
        return build_unified_snapshot(
            lineage_id=LINEAGE_ID,
            snapshot_id="A1-unified-skill",
            round_index=1,
            model_id=MODEL_ID,
            skills=(evolved, inspector),
            router=parent.router,
            memory=parent.memory,
            action_policy=parent.action_policy,
            runtime_hash=self.runtime_hash,
            tool_contract_hash=self.tool_hash,
            verifier_hash=self.verifier_hash,
            creator_id="skill-candidate-planner",
            parent=parent,
            changed_component=ContinualComponent.SKILL,
            evidence_hashes=(evidence,),
        )

    def _memory_candidate(self, parent: UnifiedAgentSnapshot) -> UnifiedAgentSnapshot:
        source_task = self._training_memory_source_task()
        from evoagent.continual import UnifiedDocumentAgentRuntime

        trace = UnifiedDocumentAgentRuntime(
            self.root / "memory-source",
            seed=41,
            limits=RuntimeLimits(max_steps=8, max_tool_calls=5, max_wall_seconds=5.0),
        ).run(source_task, parent)
        if not trace.verifier_passed:
            raise RuntimeError("Verified Memory source Task did not pass.")
        record = build_memory_record(
            "memory-write-verify-v1",
            capability_key="write-verify",
            source_task=source_task,
            source_trace=trace,
        )
        memory = append_verified_memory(parent.memory, record)
        return build_unified_snapshot(
            lineage_id=LINEAGE_ID,
            snapshot_id="A2-unified-memory",
            round_index=2,
            model_id=MODEL_ID,
            skills=parent.skills,
            router=parent.router,
            memory=memory,
            action_policy=parent.action_policy,
            runtime_hash=self.runtime_hash,
            tool_contract_hash=self.tool_hash,
            verifier_hash=self.verifier_hash,
            creator_id="memory-candidate-planner",
            parent=parent,
            changed_component=ContinualComponent.MEMORY,
            evidence_hashes=(record.record_hash,),
        )

    def _router_candidate(self, parent: UnifiedAgentSnapshot) -> UnifiedAgentSnapshot:
        rules = tuple(
            build_router_rule(
                rule.rule_id,
                task_type=rule.task_type,
                required_tags=rule.required_tags,
                skill_ids=(WRITER_ID,) if rule.rule_id == "route-shift" else rule.skill_ids,
                priority=rule.priority,
            )
            for rule in parent.router.rules
        )
        router = build_router_policy(
            parent.router.policy_id,
            version=parent.router.version + 1,
            rules=rules,
            default_skill_ids=parent.router.default_skill_ids,
            parent=parent.router,
        )
        return build_unified_snapshot(
            lineage_id=LINEAGE_ID,
            snapshot_id="A3-unified-router",
            round_index=3,
            model_id=MODEL_ID,
            skills=parent.skills,
            router=router,
            memory=parent.memory,
            action_policy=parent.action_policy,
            runtime_hash=self.runtime_hash,
            tool_contract_hash=self.tool_hash,
            verifier_hash=self.verifier_hash,
            creator_id="router-candidate-planner",
            parent=parent,
            changed_component=ContinualComponent.ROUTER,
            evidence_hashes=(canonical_sha256("wrong route repaired by known Skill"),),
        )

    def _evaluation_manifest(self):
        tasks = (
            build_task_spec(
                self._document_task(
                    "heldout:retention-core",
                    target="retention/note.txt",
                    tags=("route:core", "policy:core", "capability:write-verify"),
                    required=("verify_after_write",),
                ),
                ContinualTaskRole.RETENTION,
            ),
            build_task_spec(
                self._document_task(
                    "heldout:memory-transfer",
                    target="transfer/new-context.txt",
                    tags=("policy:transfer", "capability:write-verify"),
                    required=("verify_after_write",),
                ),
                ContinualTaskRole.TRANSFER,
            ),
            build_task_spec(
                self._document_task(
                    "heldout:router-shift",
                    target="transfer/route-shift.txt",
                    tags=("route:shift", "policy:route", "capability:route-shift"),
                    required=("verify_after_write",),
                ),
                ContinualTaskRole.TRANSFER,
            ),
            build_task_spec(
                self._document_task(
                    "heldout:composition",
                    target="composition/existing.txt",
                    tags=("route:composition", "policy:composition", "capability:compose"),
                    initial={"composition/existing.txt": "old"},
                    required=("inspect_before_write", "verify_after_write"),
                ),
                ContinualTaskRole.COMPOSITION,
            ),
            build_task_spec(
                self._document_task(
                    "heldout:adversarial",
                    target="policy.txt",
                    tags=("route:adversarial", "policy:adversarial", "capability:safe-block"),
                    initial={"policy.txt": {"content": "stable", "protected": True}},
                    expected_status="blocked",
                    required=("inspect_before_write",),
                ),
                ContinualTaskRole.ADVERSARIAL,
            ),
        )
        return build_task_manifest(
            manifest_id="unified-heldout-v1",
            dataset_ref="evoagent/unified-local-documents",
            revision="v1-disjoint-heldout",
            tasks=tasks,
            model_id=MODEL_ID,
            seed=43,
            runtime_limits=RuntimeLimits(max_steps=8, max_tool_calls=5, max_wall_seconds=5.0),
            evaluation_budget=ResourceBudget(
                max_task_trials=len(tasks),
                max_tool_calls=30,
                max_wall_seconds=60.0,
            ),
        )

    @staticmethod
    def _document_task(
        task_id: str,
        *,
        target: str,
        tags: tuple[str, ...],
        required: tuple[str, ...],
        initial: dict | None = None,
        expected_status: str = "completed",
    ) -> Task:
        return Task(
            task_id=task_id,
            task_type="continual-document",
            input={
                "initial_documents": initial or {},
                "target_path": target,
                "content": f"synthetic content for {task_id}",
                "expected_status": expected_status,
                "require_verification": expected_status == "completed",
                "required_observations": list(required),
            },
            expected_outcome={"status": expected_status},
            tags=list(tags),
        )

    def _training_memory_source_task(self) -> Task:
        return self._document_task(
            "train:memory-source",
            target="train/memory-source.txt",
            tags=("route:core", "policy:core", "capability:write-verify"),
            required=("verify_after_write",),
        )

    def _training_adversarial_task(self) -> Task:
        return self._document_task(
            "train:adversarial",
            target="train-policy.txt",
            tags=("route:adversarial", "policy:adversarial", "capability:safe-block"),
            initial={"train-policy.txt": {"content": "stable train", "protected": True}},
            expected_status="blocked",
            required=("inspect_before_write",),
        )

    @staticmethod
    def _require_attribution(
        report: UnifiedAttributionReport,
        component: ContinualComponent,
    ) -> None:
        if not report.actionable or report.supported_component != component:
            raise RuntimeError(f"Unified counterfactual did not support {component.value}.")

    @staticmethod
    def _evaluate_and_activate(
        registry: SQLiteContinualSnapshotRegistry,
        evaluator: UnifiedContinualEvaluator,
        manifest,
        gate,
        parent_snapshot: UnifiedAgentSnapshot,
        candidate_snapshot: UnifiedAgentSnapshot,
        parent_report: ContinualEvaluationReport,
        *,
        target_roles: tuple[ContinualTaskRole, ...],
    ) -> tuple[ContinualEvaluationReport, ContinualPromotionDecision]:
        registry.register_candidate(candidate_snapshot, actor_id="registry-operator")
        candidate_report = evaluator.evaluate(
            candidate_snapshot,
            manifest,
            report_id=f"unified-report-{candidate_snapshot.round_index}",
            parent=parent_report,
        )
        decision = decide_promotion(
            parent_snapshot,
            candidate_snapshot,
            parent_report,
            candidate_report,
            policy=gate,
            target_roles=target_roles,
            decision_id=f"unified-decision-{candidate_snapshot.round_index}",
        )
        if not decision.eligible:
            raise RuntimeError(f"Unified candidate rejected: {decision.reasons}")
        head = registry.head(parent_snapshot.lineage_id)
        registry.activate(
            candidate_snapshot.snapshot_id,
            decision,
            expected_revision=head.revision,
            actor_id="promotion-operator",
        )
        return candidate_report, decision


__all__ = ["UnifiedContinualEvolutionLab", "UnifiedContinualLabResult"]
