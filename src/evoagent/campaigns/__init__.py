from .governance import CampaignGovernanceService
from .models import (
    ApprovalDecision,
    CampaignApproval,
    CampaignAuditEvent,
    CampaignCheckpoint,
    CampaignRecord,
    CampaignReservation,
    CampaignRisk,
    CampaignState,
    CampaignType,
    ModelEvidenceSnapshot,
)
from .operator import CampaignOperatorView
from .policy import CampaignApprovalPolicy
from .repository import (
    CampaignApprovalError,
    CampaignAuditIntegrityError,
    CampaignConflictError,
    CampaignCooldownError,
    InvalidCampaignTransition,
    SQLiteCampaignRepository,
    StaleCampaignRevision,
    fingerprint_payload,
)


def __getattr__(name: str):
    if name == "PersistentModelEvidenceAccumulator":
        from .evidence import PersistentModelEvidenceAccumulator

        return PersistentModelEvidenceAccumulator
    if name in {"GovernedEvolutionCycleResult", "GovernedEvolutionCycleService"}:
        from .cycle import GovernedEvolutionCycleResult, GovernedEvolutionCycleService

        return {
            "GovernedEvolutionCycleResult": GovernedEvolutionCycleResult,
            "GovernedEvolutionCycleService": GovernedEvolutionCycleService,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "ApprovalDecision",
    "CampaignApproval",
    "CampaignApprovalError",
    "CampaignApprovalPolicy",
    "CampaignAuditEvent",
    "CampaignAuditIntegrityError",
    "CampaignCheckpoint",
    "CampaignConflictError",
    "CampaignCooldownError",
    "CampaignGovernanceService",
    "CampaignOperatorView",
    "CampaignRecord",
    "CampaignReservation",
    "CampaignRisk",
    "CampaignState",
    "CampaignType",
    "GovernedEvolutionCycleResult",
    "GovernedEvolutionCycleService",
    "InvalidCampaignTransition",
    "ModelEvidenceSnapshot",
    "PersistentModelEvidenceAccumulator",
    "SQLiteCampaignRepository",
    "StaleCampaignRevision",
    "fingerprint_payload",
]
