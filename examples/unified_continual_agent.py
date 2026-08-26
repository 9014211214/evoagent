from __future__ import annotations

import json
import tempfile
from pathlib import Path

from evoagent.lab import UnifiedContinualEvolutionLab


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="evoagent-unified-") as root:
        result = UnifiedContinualEvolutionLab(Path(root)).run()
        print(json.dumps(result.model_dump(mode="json"), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
