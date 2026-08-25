from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from evoagent import __version__
from evoagent._io import atomic_temporary_path
from evoagent.campaigns.governance import CampaignGovernanceService
from evoagent.campaigns.models import (
    ApprovalDecision,
    CampaignCheckpoint,
    CampaignState,
    CampaignType,
)
from evoagent.campaigns.operator import CampaignOperatorView
from evoagent.campaigns.repository import SQLiteCampaignRepository
from evoagent.compliance import ThirdPartyComplianceVerifier
from evoagent.execution import (
    ExecutionAuthorizationManager,
    ExecutionInvocation,
)
from evoagent.runs import ReproducibleRunBundleManager, RunManifestCheckpoint
from evoagent.skills.bundle import SkillStateBundleManager
from evoagent.skills.persistent_models import SkillRegistryCheckpoint
from evoagent.skills.sqlite_registry import SQLiteSkillRegistry


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    return value


def _write_json(value: Any, *, stream=None) -> None:
    stream = stream or sys.stdout
    stream.write(json.dumps(_jsonable(value), sort_keys=True, ensure_ascii=False) + "\n")


def _load_json_model(path: str, model_type):
    target = _require_existing_file(path, label=model_type.__name__)
    return model_type.model_validate_json(target.read_text(encoding="utf-8"))


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Datetime values must include a timezone.")
    return parsed


def _require_existing_database(path: str) -> Path:
    database = Path(path)
    if not database.is_file():
        raise FileNotFoundError(f"Database does not exist: {database}")
    return database


def _require_existing_file(path: str, *, label: str) -> Path:
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise FileNotFoundError(f"{label} does not exist as a regular file: {target}")
    return target


def _atomic_write_text(path: str | Path, content: str) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = atomic_temporary_path(destination)
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


def _require_nonempty(value: str, *, field: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty.")
    return normalized


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evoagent")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    groups = parser.add_subparsers(dest="group", required=True)

    skill = groups.add_parser("skill", help="Inspect or transfer persistent Skill state.")
    skill_commands = skill.add_subparsers(dest="command", required=True)

    skill_list = skill_commands.add_parser("list")
    skill_list.add_argument("--db", required=True)

    skill_show = skill_commands.add_parser("show")
    skill_show.add_argument("--db", required=True)
    skill_show.add_argument("--skill-id", required=True)
    skill_show.add_argument("--version")

    skill_events = skill_commands.add_parser("events")
    skill_events.add_argument("--db", required=True)
    skill_events.add_argument("--skill-id")

    skill_export = skill_commands.add_parser("export")
    skill_export.add_argument("--db", required=True)
    skill_export.add_argument("--out", required=True)

    skill_import = skill_commands.add_parser("import")
    skill_import.add_argument("--db", required=True)
    skill_import.add_argument("--input", required=True)

    skill_checkpoint = skill_commands.add_parser("checkpoint")
    skill_checkpoint.add_argument("--db", required=True)
    skill_checkpoint.add_argument("--out", required=True)

    skill_audit = skill_commands.add_parser("audit-verify")
    skill_audit.add_argument("--db", required=True)
    skill_audit.add_argument("--checkpoint")

    campaign = groups.add_parser("campaign", help="Inspect and approve Campaigns.")
    campaign_commands = campaign.add_subparsers(dest="command", required=True)

    campaign_list = campaign_commands.add_parser("list")
    campaign_list.add_argument("--db", required=True)
    campaign_list.add_argument("--state", choices=[item.value for item in CampaignState])
    campaign_list.add_argument("--type", choices=[item.value for item in CampaignType])

    campaign_show = campaign_commands.add_parser("show")
    campaign_show.add_argument("--db", required=True)
    campaign_show.add_argument("--campaign-id", required=True)

    campaign_approvals = campaign_commands.add_parser("approvals")
    campaign_approvals.add_argument("--db", required=True)
    campaign_approvals.add_argument("--campaign-id", required=True)

    campaign_checkpoint = campaign_commands.add_parser("checkpoint")
    campaign_checkpoint.add_argument("--db", required=True)
    campaign_checkpoint.add_argument("--out", required=True)

    campaign_audit = campaign_commands.add_parser("audit-verify")
    campaign_audit.add_argument("--db", required=True)
    campaign_audit.add_argument("--checkpoint")

    for name in ("approve", "reject"):
        command = campaign_commands.add_parser(name)
        command.add_argument("--db", required=True)
        command.add_argument("--campaign-id", required=True)
        command.add_argument("--actor", required=True)
        command.add_argument("--reason", required=True)
        command.add_argument("--expected-revision", required=True, type=int)
        command.add_argument("--cooldown-seconds", type=int, default=0)

    run = groups.add_parser("run", help="Inspect and verify reproducible run bundles.")
    run_commands = run.add_subparsers(dest="command", required=True)
    for name in ("show", "verify"):
        command = run_commands.add_parser(name)
        command.add_argument("--bundle", required=True)
        command.add_argument("--checkpoint")
    run_checkpoint = run_commands.add_parser("checkpoint")
    run_checkpoint.add_argument("--bundle", required=True)
    run_checkpoint.add_argument("--out", required=True)

    compliance = groups.add_parser(
        "compliance", help="Inspect and verify the pinned third-party lock."
    )
    compliance_commands = compliance.add_subparsers(dest="command", required=True)
    compliance_show = compliance_commands.add_parser("show")
    compliance_show.add_argument("--lock", default="THIRD_PARTY_LOCK.json")
    compliance_verify = compliance_commands.add_parser("verify")
    compliance_verify.add_argument("--lock", default="THIRD_PARTY_LOCK.json")
    compliance_verify.add_argument("--notices", default="THIRD_PARTY_NOTICES.md")

    execution = groups.add_parser(
        "execution",
        help="Create and inspect execution requests and run offline preflight checks.",
    )
    execution_commands = execution.add_subparsers(dest="command", required=True)

    execution_request = execution_commands.add_parser("request")
    execution_request.add_argument("--invocation", required=True)
    execution_request.add_argument("--request-id", required=True)
    execution_request.add_argument("--requester", required=True)
    execution_request.add_argument("--purpose", required=True)
    execution_request.add_argument("--issued-at", required=True)
    execution_request.add_argument("--expires-at", required=True)
    execution_request.add_argument("--out", required=True)

    execution_show_request = execution_commands.add_parser("show-request")
    execution_show_request.add_argument("--request", required=True)

    execution_show_authorization = execution_commands.add_parser("show-authorization")
    execution_show_authorization.add_argument("--authorization", required=True)

    execution_preflight = execution_commands.add_parser("preflight")
    execution_preflight.add_argument("--authorization", required=True)
    execution_preflight.add_argument("--invocation", required=True)
    execution_preflight.add_argument("--now")

    return parser


def _run_skill(args) -> Any:
    manager = SkillStateBundleManager()
    if args.command == "import":
        bundle = manager.load_file(args.input)
        registry = SQLiteSkillRegistry(args.db)
        manager.import_into(registry, bundle)
        return {"imported": str(Path(args.input).resolve()), "manifest_hash": bundle.manifest_hash}

    database = _require_existing_database(args.db)
    registry = SQLiteSkillRegistry(database)
    if args.command == "list":
        return [
            {
                "skill_id": skill_id,
                "active_version": registry.active(skill_id).spec.version,
                "active_revision": registry.active_revision(skill_id),
                "versions": len(registry.list_versions(skill_id)),
            }
            for skill_id in registry.list_skill_ids()
        ]
    if args.command == "show":
        return (
            registry.get(args.skill_id, args.version)
            if args.version
            else registry.active(args.skill_id)
        )
    if args.command == "events":
        return registry.events(args.skill_id)
    if args.command == "export":
        bundle = manager.export_file(registry, args.out)
        return {"exported": str(Path(args.out).resolve()), "manifest_hash": bundle.manifest_hash}
    if args.command == "checkpoint":
        checkpoint = registry.checkpoint()
        _atomic_write_text(args.out, checkpoint.model_dump_json(indent=2) + "\n")
        return checkpoint
    if args.command == "audit-verify":
        checkpoint = (
            _load_json_model(args.checkpoint, SkillRegistryCheckpoint)
            if args.checkpoint
            else None
        )
        return {"verified": registry.verify_audit(checkpoint)}
    raise ValueError(f"Unsupported Skill command: {args.command}")


def _run_campaign(args) -> Any:
    database = _require_existing_database(args.db)
    repository = SQLiteCampaignRepository(database)
    if args.command == "list":
        return CampaignOperatorView(repository).list_campaigns(
            state=CampaignState(args.state) if args.state else None,
            campaign_type=CampaignType(args.type) if args.type else None,
        )
    if args.command == "show":
        return repository.get(args.campaign_id)
    if args.command == "approvals":
        return repository.approvals(args.campaign_id)
    if args.command == "checkpoint":
        checkpoint = repository.checkpoint()
        _atomic_write_text(args.out, checkpoint.model_dump_json(indent=2) + "\n")
        return checkpoint
    if args.command == "audit-verify":
        checkpoint = (
            _load_json_model(args.checkpoint, CampaignCheckpoint)
            if args.checkpoint
            else None
        )
        return {"verified": repository.verify_audit(checkpoint)}
    if args.command in {"approve", "reject"}:
        if args.expected_revision < 0:
            raise ValueError("expected revision must be non-negative.")
        if args.cooldown_seconds < 0:
            raise ValueError("cooldown seconds must be non-negative.")
        decision = (
            ApprovalDecision.APPROVE if args.command == "approve" else ApprovalDecision.REJECT
        )
        return CampaignGovernanceService(repository).approve(
            _require_nonempty(args.campaign_id, field="campaign ID"),
            actor_id=_require_nonempty(args.actor, field="actor"),
            decision=decision,
            reason=_require_nonempty(args.reason, field="reason"),
            expected_revision=args.expected_revision,
            rejection_cooldown_seconds=args.cooldown_seconds,
        )
    raise ValueError(f"Unsupported Campaign command: {args.command}")


def _run_run_bundle(args) -> Any:
    manager = ReproducibleRunBundleManager()
    if args.command == "checkpoint":
        checkpoint = manager.checkpoint(args.bundle)
        _atomic_write_text(args.out, checkpoint.model_dump_json(indent=2) + "\n")
        return checkpoint
    checkpoint = (
        _load_json_model(args.checkpoint, RunManifestCheckpoint)
        if args.checkpoint
        else None
    )
    verification = manager.verify(args.bundle, checkpoint=checkpoint)
    if args.command == "verify":
        return verification
    if args.command == "show":
        return {
            "verification": verification,
            "manifest": manager.load_manifest(args.bundle),
        }
    raise ValueError(f"Unsupported run command: {args.command}")


def _run_compliance(args) -> Any:
    verifier = ThirdPartyComplianceVerifier()
    _require_existing_file(args.lock, label="Third-party lock")
    if args.command == "show":
        return verifier.load_lock(args.lock)
    if args.command == "verify":
        _require_existing_file(args.notices, label="Third-party notices")
        return verifier.verify(lock_path=args.lock, notices_path=args.notices)
    raise ValueError(f"Unsupported compliance command: {args.command}")


def _run_execution(args) -> Any:
    manager = ExecutionAuthorizationManager()
    if args.command == "request":
        invocation = _load_json_model(args.invocation, ExecutionInvocation)
        request = manager.prepare_request(
            request_id=_require_nonempty(args.request_id, field="request ID"),
            requester_id=_require_nonempty(args.requester, field="requester"),
            purpose=_require_nonempty(args.purpose, field="purpose"),
            issued_at=_parse_datetime(args.issued_at),
            expires_at=_parse_datetime(args.expires_at),
            invocation=invocation,
        )
        manager.write_request(request, args.out)
        return {
            "request": str(Path(args.out).resolve()),
            "request_hash": request.request_hash,
            "required_approvals": manager.required_approvals(invocation),
        }
    if args.command == "show-request":
        return manager.load_request(args.request)
    if args.command == "show-authorization":
        return manager.load_authorization(args.authorization)
    if args.command == "preflight":
        authorization = manager.load_authorization(args.authorization)
        invocation = _load_json_model(args.invocation, ExecutionInvocation)
        return manager.preflight(
            authorization,
            invocation,
            now=_parse_datetime(args.now) if args.now else None,
        )
    raise ValueError(f"Unsupported execution command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        handlers = {
            "skill": _run_skill,
            "campaign": _run_campaign,
            "run": _run_run_bundle,
            "compliance": _run_compliance,
            "execution": _run_execution,
        }
        result = handlers[args.group](args)
        _write_json(result)
        return 0
    except Exception as exc:
        _write_json({"error": type(exc).__name__, "message": str(exc)}, stream=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
