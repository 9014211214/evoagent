from __future__ import annotations

from datetime import datetime, timezone

from evoagent.program.constraints import validate_single_release_package_budget
from evoagent.program.models import (
    GenerationOutcome,
    GenerationPlan,
    GenerationRecord,
    GenerationStatus,
    ProgramDecision,
    ProgramEventType,
    ProgramLearningSignal,
    ProgramState,
)
from evoagent.program.repository import (
    ProgramConflictError,
    SQLiteEvolutionProgramRepository,
)


class HardenedSQLiteEvolutionProgramRepository(
    SQLiteEvolutionProgramRepository
):
    """Close duplicate accounting and bind audit events to stored lineage."""

    def store_signal(
        self,
        signal: ProgramLearningSignal,
        *,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[ProgramLearningSignal, bool]:
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                self._require_program(connection, signal.program_id)
                generation = connection.execute(
                    "SELECT generation_id FROM program_generations "
                    "WHERE program_id = ? AND generation_index = ?",
                    (signal.program_id, signal.generation_index),
                ).fetchone()
                if generation is None:
                    raise ValueError(
                        "Learning signal generation is not registered in the Program."
                    )
                row = connection.execute(
                    "SELECT signal_json FROM program_signals WHERE signal_id = ?",
                    (signal.signal_id,),
                ).fetchone()
                if row is not None:
                    existing = ProgramLearningSignal.model_validate_json(
                        row["signal_json"]
                    )
                    if existing != signal:
                        raise ProgramConflictError(
                            "Learning signal ID has conflicting content."
                        )
                    connection.rollback()
                    return existing, True
                connection.execute(
                    "INSERT INTO program_signals "
                    "(signal_id, program_id, signal_hash, signal_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        signal.signal_id,
                        signal.program_id,
                        signal.signal_hash,
                        signal.model_dump_json(),
                        now.isoformat(),
                    ),
                )
                self._append_event(
                    connection,
                    program_id=signal.program_id,
                    generation_id=generation["generation_id"],
                    event_type=ProgramEventType.SIGNAL_STORED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={
                        "signal_id": signal.signal_id,
                        "signal_hash": signal.signal_hash,
                        "causal_attribution_claimed": False,
                    },
                    created_at=now,
                )
                connection.commit()
                return signal, False
            except Exception:
                connection.rollback()
                raise

    def plan_generation(
        self,
        plan: GenerationPlan,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> tuple[GenerationRecord, bool]:
        validate_single_release_package_budget(plan.budget)
        try:
            existing = self.get_generation(plan.program_id, plan.generation_id)
        except KeyError:
            existing = None
        if existing is not None:
            if existing.plan != plan:
                raise ProgramConflictError(
                    "Generation ID has conflicting immutable plan."
                )
            return existing, True
        program = self.get_program(plan.program_id)
        head = self.head(plan.program_id)
        remaining_pairs = program.policy.budget.max_total_pairs - head.total_pairs
        remaining_tokens = program.policy.budget.max_total_tokens - head.total_tokens
        remaining_cost = (
            program.policy.budget.max_total_cost_usd - head.total_cost_usd
        )
        if (
            plan.budget.max_pairs > remaining_pairs
            or plan.budget.max_tokens > remaining_tokens
            or plan.budget.max_cost_usd > remaining_cost + 1e-12
        ):
            raise ValueError(
                "GenerationPlan reserves more than the remaining cumulative Program budget."
            )
        return super().plan_generation(
            plan,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason,
            now=now,
        )

    def bind_campaign(
        self,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        existing = self.get_generation(program_id, generation_id)
        if existing.campaign_id == campaign_id:
            return existing
        return super().bind_campaign(
            program_id,
            generation_id,
            campaign_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason,
            now=now,
        )

    def authorize_generation(
        self,
        program_id: str,
        generation_id: str,
        campaign_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        existing = self.get_generation(program_id, generation_id)
        if (
            existing.campaign_id == campaign_id
            and existing.status
            in {
                GenerationStatus.AUTHORIZED,
                GenerationStatus.RUNNING,
                GenerationStatus.COMPLETED,
                GenerationStatus.ROLLED_BACK,
                GenerationStatus.HELD,
            }
        ):
            return existing
        return super().authorize_generation(
            program_id,
            generation_id,
            campaign_id,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason,
            now=now,
        )

    def start_generation(
        self,
        program_id: str,
        generation_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        existing = self.get_generation(program_id, generation_id)
        if existing.status in {
            GenerationStatus.RUNNING,
            GenerationStatus.COMPLETED,
            GenerationStatus.ROLLED_BACK,
            GenerationStatus.HELD,
        }:
            return existing
        now = now or datetime.now(timezone.utc)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                head = self._require_head(connection, program_id)
                self._check_revision(head, expected_revision)
                record = self._require_generation(
                    connection,
                    program_id,
                    generation_id,
                )
                if record.status != GenerationStatus.AUTHORIZED:
                    raise ValueError("Only an authorized generation may start.")
                if record.plan is None:
                    raise ValueError(
                        "Authorized successor generation lacks its immutable plan."
                    )
                connection.execute(
                    "UPDATE program_generations SET status = ?, updated_at = ? "
                    "WHERE program_id = ? AND generation_id = ?",
                    (
                        GenerationStatus.RUNNING.value,
                        now.isoformat(),
                        program_id,
                        generation_id,
                    ),
                )
                self._set_program_state(
                    connection,
                    program_id,
                    ProgramState.GENERATION_RUNNING,
                    now,
                )
                self._update_head(
                    connection,
                    head,
                    state=ProgramState.GENERATION_RUNNING.value,
                    current_generation_index=record.generation_index,
                    active_generation_id=generation_id,
                    revision=int(head["revision"]) + 1,
                    updated_at=now,
                )
                self._append_event(
                    connection,
                    program_id=program_id,
                    generation_id=generation_id,
                    event_type=ProgramEventType.GENERATION_STARTED,
                    actor_id=actor_id,
                    reason=reason,
                    payload={"plan_hash": record.plan.plan_hash},
                    created_at=now,
                )
                connection.commit()
                return self.get_generation(program_id, generation_id)
            except Exception:
                connection.rollback()
                raise

    def complete_generation(
        self,
        outcome: GenerationOutcome,
        *,
        expected_revision: int,
        actor_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> GenerationRecord:
        existing = self.get_generation(outcome.program_id, outcome.generation_id)
        if existing.outcome is not None:
            if existing.outcome != outcome:
                raise ProgramConflictError(
                    "Generation already completed with different immutable evidence."
                )
            return existing
        return super().complete_generation(
            outcome,
            expected_revision=expected_revision,
            actor_id=actor_id,
            reason=reason,
            now=now,
        )

    def store_decision(
        self,
        decision: ProgramDecision,
        *,
        expected_revision: int,
        actor_id: str,
        now: datetime | None = None,
    ) -> tuple[ProgramDecision, bool]:
        matches = [
            item
            for item in self.list_decisions(decision.program_id)
            if item.decision_id == decision.decision_id
        ]
        if matches:
            if len(matches) != 1 or matches[0] != decision:
                raise ProgramConflictError(
                    "Program decision ID has conflicting immutable content."
                )
            return matches[0], True
        return super().store_decision(
            decision,
            expected_revision=expected_revision,
            actor_id=actor_id,
            now=now,
        )

    def verify_state(self, program_id: str) -> bool:
        super().verify_state(program_id)
        head = self.head(program_id)
        generations = self.list_generations(program_id)
        expected_campaign_count = sum(
            item.campaign_id is not None for item in generations
        )
        if head.generation_campaign_count != expected_campaign_count:
            raise ProgramConflictError(
                "Program head Campaign count differs from generation bindings."
            )
        for generation in generations[1:]:
            if generation.plan is None:
                raise ProgramConflictError(
                    "Successor generation is missing its immutable plan."
                )
            try:
                validate_single_release_package_budget(generation.plan.budget)
            except ValueError as exc:
                raise ProgramConflictError(
                    "Generation plan release-package budget is not representable."
                ) from exc
        return True


__all__ = ["HardenedSQLiteEvolutionProgramRepository"]
