"""Optional SEAGym/Harbor integration for EvoAgent."""

from .baseline import EvoAgentSEAGymBaseline
from .harbor_agent import EvoAgentMiMo
from .models import HarnessSnapshot

__all__ = ["EvoAgentMiMo", "EvoAgentSEAGymBaseline", "HarnessSnapshot"]
__version__ = "0.1.0"
