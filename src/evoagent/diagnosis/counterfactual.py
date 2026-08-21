from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from evoagent.domain.models import EvolutionAction, FailureLayer


class ExperimentType(str, Enum):
    REPLACE_SKILL = "replace_skill"
    FORCE_ROUTER = "force_router"
    REPLAY_TOOL = "replay_tool"
    COMPLETE_CONTEXT = "complete_context"
    ORACLE_VERIFIER = "oracle_verifier"
    RESET_ENVIRONMENT = "reset_environment"
    REFERENCE_MODEL = "reference_model"


class FailureHypothesis(BaseModel):
    hypothesis_id: str
    layer: FailureLayer
    description: str
    prior_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    experiment_type: ExperimentType


class CounterfactualExperiment(BaseModel):
    experiment_id: str
    hypothesis_id: str
    experiment_type: ExperimentType
    intervention: dict[str, Any] = Field(default_factory=dict)
    controlled_variables: list[str] = Field(default_factory=list)


class ExperimentResult(BaseModel):
    experiment_id: str
    hypothesis_id: str
    experiment_type: ExperimentType
    baseline_success: bool
    counterfactual_success: bool
    supports_hypothesis: bool
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CounterfactualRunner(ABC):
    @abstractmethod
    def run(self, experiment: CounterfactualExperiment) -> ExperimentResult:
        raise NotImplementedError


class LayerScore(BaseModel):
    layer: FailureLayer
    score: float = Field(ge=0.0, le=1.0)
    supporting_experiment_ids: list[str] = Field(default_factory=list)


class AttributionReport(BaseModel):
    root_cause_layer: FailureLayer
    confidence: float = Field(ge=0.0, le=1.0)
    ranked_causes: list[LayerScore] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    experiments: list[ExperimentResult] = Field(default_factory=list)
    recommended_action: EvolutionAction
    actionable: bool
    reason: str
