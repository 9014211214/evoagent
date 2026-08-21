from __future__ import annotations

import argparse
import json
import os

from evoagent.lab import DEFAULT_THIRD_PARTY_LOCK_HASH, ReferenceEvolutionLab


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evoagent.lab")
    parser.add_argument("--root", required=True)
    parser.add_argument(
        "--source-commit",
        default=os.environ.get("GITHUB_SHA", "0" * 40),
    )
    parser.add_argument(
        "--third-party-lock-hash",
        default=DEFAULT_THIRD_PARTY_LOCK_HASH,
        help="Pinned SHA-256 of the reviewed THIRD_PARTY_LOCK.json evidence.",
    )
    args = parser.parse_args(argv)
    result = ReferenceEvolutionLab(
        args.root,
        source_commit=args.source_commit,
        third_party_lock_hash=args.third_party_lock_hash,
    ).run()
    print(json.dumps(result.model_dump(mode="json"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
