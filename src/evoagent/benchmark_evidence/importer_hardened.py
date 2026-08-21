from __future__ import annotations

from typing import Any

from evoagent.benchmark_evidence.importer import (
    HarborResultImportError,
    HarborResultImporter as _BaseHarborResultImporter,
)
from evoagent.benchmark_evidence.models import (
    BenchmarkRunContract,
    BenchmarkRunEvidence,
)


class HarborResultImporter(_BaseHarborResultImporter):
    """Public importer with deterministic structural error precedence.

    A result whose declared ``n_total_trials`` differs from the actual
    ``trial_results`` array is first classified as a count-integrity failure.
    This remains distinct from a structurally consistent but still-running or
    pending Harbor job.
    """

    def _parse_job(
        self,
        payload: dict[str, Any],
        *,
        evidence_id: str,
        source_file_sha256: str,
        contract: BenchmarkRunContract,
    ) -> BenchmarkRunEvidence:
        declared_total = payload.get("n_total_trials")
        trial_payloads = payload.get("trial_results")
        if (
            isinstance(declared_total, int)
            and not isinstance(declared_total, bool)
            and isinstance(trial_payloads, list)
            and len(trial_payloads) != declared_total
        ):
            raise HarborResultImportError(
                "Harbor declared total differs from the number of trial results."
            )
        return super()._parse_job(
            payload,
            evidence_id=evidence_id,
            source_file_sha256=source_file_sha256,
            contract=contract,
        )


__all__ = [
    "HarborResultImportError",
    "HarborResultImporter",
]
