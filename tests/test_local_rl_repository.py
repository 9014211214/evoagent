import sqlite3

import pytest

from evoagent.lab.local_agentic_rl import LocalAgenticRLTrainingLab
from evoagent.local_rl import (
    LocalRLAuditIntegrityError,
    LocalRLRepositoryConflictError,
    SQLiteLocalRLRepository,
    build_hyperparameters,
    build_run_manifest,
)


def test_identical_manifest_reuses_without_new_event(tmp_path):
    lab = LocalAgenticRLTrainingLab(tmp_path / "lab")
    manifest = lab.build_manifest()
    repository = SQLiteLocalRLRepository(lab.database_path)

    assert repository.register_manifest(
        manifest,
        actor_id="test",
        now=manifest.created_at,
    ) is False
    checkpoint = repository.checkpoint()
    assert repository.register_manifest(
        manifest,
        actor_id="test",
        now=manifest.created_at,
    ) is True
    assert repository.checkpoint() == checkpoint


def test_conflicting_manifest_under_same_run_id_fails_closed(tmp_path):
    lab = LocalAgenticRLTrainingLab(tmp_path / "lab")
    manifest = lab.build_manifest()
    repository = SQLiteLocalRLRepository(lab.database_path)
    repository.register_manifest(
        manifest,
        actor_id="test",
        now=manifest.created_at,
    )
    changed_hyperparameters = build_hyperparameters(
        learning_rate=0.3,
        clip_epsilon=manifest.hyperparameters.clip_epsilon,
        entropy_coefficient=manifest.hyperparameters.entropy_coefficient,
        max_gradient_norm=manifest.hyperparameters.max_gradient_norm,
        update_epochs=manifest.hyperparameters.update_epochs,
        group_size=manifest.hyperparameters.group_size,
        seed=manifest.hyperparameters.seed,
        retained_checkpoint_interval=(
            manifest.hyperparameters.retained_checkpoint_interval
        ),
    )
    conflicting = build_run_manifest(
        run_id=manifest.run_id,
        created_at=manifest.created_at,
        environment=manifest.environment,
        training_tasks=manifest.training_tasks,
        held_out_tasks=manifest.held_out_tasks,
        hyperparameters=changed_hyperparameters,
        budget=manifest.budget,
    )
    with pytest.raises(LocalRLRepositoryConflictError, match="another manifest"):
        repository.register_manifest(
            conflicting,
            actor_id="test",
            now=manifest.created_at,
        )


def test_local_rl_audit_content_and_tail_truncation_are_detected(tmp_path):
    content_lab = LocalAgenticRLTrainingLab(
        tmp_path / "content",
        source_commit="b" * 40,
    )
    content_lab.run()
    repository = SQLiteLocalRLRepository(content_lab.database_path)
    checkpoint = repository.checkpoint()
    with sqlite3.connect(content_lab.database_path) as connection:
        connection.execute(
            "UPDATE local_rl_audit_events SET reason = ? WHERE sequence = 1",
            ("tampered",),
        )
        connection.commit()
    with pytest.raises(LocalRLAuditIntegrityError):
        repository.verify_audit(checkpoint)

    tail_lab = LocalAgenticRLTrainingLab(
        tmp_path / "tail",
        source_commit="c" * 40,
    )
    tail_lab.run()
    tail_repository = SQLiteLocalRLRepository(tail_lab.database_path)
    tail_checkpoint = tail_repository.checkpoint()
    with sqlite3.connect(tail_lab.database_path) as connection:
        connection.execute(
            "DELETE FROM local_rl_audit_events WHERE sequence = "
            "(SELECT MAX(sequence) FROM local_rl_audit_events)"
        )
        connection.commit()
    assert tail_repository.verify_audit() is True
    with pytest.raises(LocalRLAuditIntegrityError, match="external checkpoint"):
        tail_repository.verify_audit(tail_checkpoint)
