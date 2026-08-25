from __future__ import annotations

import os
from pathlib import Path

from evoagent._io import atomic_temporary_path
from evoagent.domain.models import EvolutionAction, FailureLayer
from evoagent.local_rl.package import LocalRLPackageManifest
from evoagent.local_rl.program_binding import (
    ProgramBoundLocalRLPackageManifest,
    ProgramLocalRLBindingError,
    ProgramLocalRLBindingManager as _CoreBindingManager,
    ProgramLocalRLExecutionTicket,
)


_EXPECTED_AUDIT_REASONS = (
    "Frozen local RL run manifest registered.",
    "Bounded local rollout optimization completed.",
    "Independent frozen held-out evaluations stored.",
    "Best safe improving local policy checkpoint selected.",
)

_LOCAL_RL_INTERVENTIONS = {
    FailureLayer.ROUTER: EvolutionAction.UPDATE_ROUTER,
    FailureLayer.CONTEXT: EvolutionAction.UPDATE_CONTEXT,
    FailureLayer.VERIFIER: EvolutionAction.REPAIR_VERIFIER,
}


class ProgramLocalRLBindingManager(_CoreBindingManager):
    """Public binding manager with semantic audit and atomic file persistence."""

    @staticmethod
    def _verify_intervention_scope(
        ticket: ProgramLocalRLExecutionTicket,
    ) -> None:
        plan = ticket.generation_plan
        expected_action = _LOCAL_RL_INTERVENTIONS.get(plan.intervention_layer)
        if expected_action is None:
            raise ProgramLocalRLBindingError(
                "Program-bound Local RL is limited to Router, Context and "
                "Verifier policy interventions."
            )
        if plan.intervention_action != expected_action:
            raise ProgramLocalRLBindingError(
                "Program-bound Local RL action differs from its policy layer."
            )

    def build_ticket(self, **kwargs) -> ProgramLocalRLExecutionTicket:
        ticket = super().build_ticket(**kwargs)
        self._verify_intervention_scope(ticket)
        return ticket

    def verify_ticket(self, ticket: ProgramLocalRLExecutionTicket) -> bool:
        self._verify_intervention_scope(ticket)
        return super().verify_ticket(ticket)

    @staticmethod
    def _verify_local_package(
        ticket: ProgramLocalRLExecutionTicket,
        package: LocalRLPackageManifest,
    ) -> None:
        _CoreBindingManager._verify_local_package(ticket, package)
        if tuple(item.reason for item in package.audit_events) != (
            _EXPECTED_AUDIT_REASONS
        ):
            raise ProgramLocalRLBindingError(
                "Local RL audit reasons differ from the governed execution lifecycle."
            )

    def export_file(
        self,
        package: ProgramBoundLocalRLPackageManifest,
        path: str | Path,
    ) -> Path:
        self.verify(package)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise ProgramLocalRLBindingError(
                "Program-bound Local RL output must not be a symlink."
            )
        temporary = atomic_temporary_path(destination)
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(package.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def load_file(
        self,
        path: str | Path,
    ) -> ProgramBoundLocalRLPackageManifest:
        target = Path(path)
        if target.is_symlink() or not target.is_file():
            raise ProgramLocalRLBindingError(
                "Program-bound Local RL package must be a regular non-symlink file."
            )
        try:
            package = ProgramBoundLocalRLPackageManifest.model_validate_json(
                target.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise ProgramLocalRLBindingError(
                f"Program-bound Local RL package is invalid: {exc}"
            ) from exc
        self.verify(package)
        return package


__all__ = ["ProgramLocalRLBindingManager"]
