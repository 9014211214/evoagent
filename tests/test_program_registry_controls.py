import sqlite3

import pytest

from evoagent.lab import MultiGenerationEvolutionProgramLab
from evoagent.model_registry.models import canonical_sha256
from evoagent.program import (
    EvolutionProgramPackageManager,
    ProgramAuditIntegrityError,
    ProgramConflictError,
    ProgramDecision,
    SQLiteEvolutionProgramRepository,
    StaleProgramRevision,
)


def _lab(tmp_path):
    lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "program-lab",
        source_commit="d" * 40,
    )
    result = lab.run()
    package = EvolutionProgramPackageManager().load_file(result.package_path)
    return lab, package


def test_program_registry_reuses_exact_decision_but_rejects_stale_new_write(tmp_path):
    lab, package = _lab(tmp_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)

    exact, reused = repository.store_decision(
        package.decisions[-1],
        expected_revision=0,
        actor_id="retry-controller",
    )
    assert reused is True
    assert exact == package.decisions[-1]

    base = package.decisions[-1]
    payload = base.model_dump(mode="json", exclude={"decision_hash"})
    payload["decision_id"] = "program-decision:stale-new-write"
    stale_new = ProgramDecision(
        **payload,
        decision_hash=canonical_sha256(payload),
    )
    with pytest.raises(StaleProgramRevision):
        repository.store_decision(
            stale_new,
            expected_revision=0,
            actor_id="stale-controller",
        )


def test_program_registry_rejects_conflicting_signal_id(tmp_path):
    lab, package = _lab(tmp_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)

    conflicting = package.signal.model_copy(
        update={
            "reasons": package.signal.reasons + ("forged_reason",),
            "signal_hash": "0" * 64,
        }
    )
    payload = conflicting.model_dump(mode="json", exclude={"signal_hash"})
    conflicting = conflicting.model_copy(
        update={"signal_hash": canonical_sha256(payload)}
    )
    with pytest.raises(ProgramConflictError):
        repository.store_signal(
            conflicting,
            actor_id="conflicting-ingestor",
            reason="conflicting signal",
        )


def test_program_state_rejects_campaign_count_tamper(tmp_path):
    lab, package = _lab(tmp_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)
    assert package.final_head.generation_campaign_count == 1

    with sqlite3.connect(lab.program_database) as connection:
        connection.execute(
            "UPDATE program_heads SET generation_campaign_count = 0 "
            "WHERE program_id = ?",
            (package.final_head.program_id,),
        )
        connection.commit()

    with pytest.raises(ProgramConflictError, match="Campaign count"):
        repository.verify_state(package.final_head.program_id)


def test_program_audit_tamper_and_tail_truncation_are_detected(tmp_path):
    lab, package = _lab(tmp_path)
    repository = SQLiteEvolutionProgramRepository(lab.program_database)

    with sqlite3.connect(lab.program_database) as connection:
        connection.execute(
            "UPDATE program_audit_events SET reason = ? WHERE sequence = 1",
            ("modified",),
        )
        connection.commit()
    with pytest.raises(ProgramAuditIntegrityError):
        repository.verify_audit()

    tail_lab = MultiGenerationEvolutionProgramLab(
        tmp_path / "tail-lab",
        source_commit="e" * 40,
    )
    tail_result = tail_lab.run()
    tail_package = EvolutionProgramPackageManager().load_file(tail_result.package_path)
    tail_repository = SQLiteEvolutionProgramRepository(tail_lab.program_database)
    with sqlite3.connect(tail_lab.program_database) as connection:
        connection.execute(
            "DELETE FROM program_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM program_audit_events)"
        )
        connection.commit()
    assert tail_repository.verify_audit() is True
    with pytest.raises(ProgramAuditIntegrityError):
        tail_repository.verify_audit(tail_package.program_checkpoint)
