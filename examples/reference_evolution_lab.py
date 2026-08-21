from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from evoagent.lab import ReferenceEvolutionLab


with TemporaryDirectory() as directory:
    root = Path(directory)
    lab = ReferenceEvolutionLab(root, source_commit="0" * 40)
    first = lab.run()
    second = lab.run()

    print("first resumed:", first.resumed)
    print("second resumed:", second.resumed)
    print("base score:", first.baseline.score)
    print("evolved score:", first.evolved.score)
    print("evolution gain:", first.evolution_gain)
    print("active version:", first.active_version)
    print("campaign state:", first.campaign_state)
    print("restart verified:", second.restart_verified)
    print("same campaign:", first.campaign_id == second.campaign_id)
    print("external execution performed:", first.external_execution_performed)
