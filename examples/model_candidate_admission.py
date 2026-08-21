from tempfile import TemporaryDirectory

from evoagent.lab import ModelCandidateAdmissionLab


with TemporaryDirectory() as directory:
    lab = ModelCandidateAdmissionLab(
        directory,
        source_commit="a" * 40,
    )
    first = lab.run()
    second = lab.run()

    print("first resumed:", first.resumed)
    print("second resumed:", second.resumed)
    print("candidate:", first.candidate_id)
    print("lifecycle:", list(first.lifecycle_statuses))
    print("held-out base score:", first.held_out_base_score)
    print("held-out candidate score:", first.held_out_candidate_score)
    print("held-out improvement:", first.held_out_improvement)
    print("replay score:", first.replay_candidate_score)
    print("retention score:", first.retention_candidate_score)
    print("safety score:", first.safety_candidate_score)
    print("regressions:", first.regression_count)
    print("safety violations:", first.safety_violation_count)
    print("candidate budget ok:", first.candidate_budget_ok)
    print("approvals:", first.approval_count)
    print("Campaign state:", first.activation_campaign_state)
    print("active after activation:", first.active_model_after_activation)
    print("revision after activation:", first.active_revision_after_activation)
    print("active after rollback:", first.active_model_after_rollback)
    print("revision after rollback:", first.active_revision_after_rollback)
    print("same package:", second.package_hash == first.package_hash)
    print("synthetic fixture:", first.synthetic_fixture)
    print("checkpoint downloaded:", first.checkpoint_downloaded)
    print("candidate weights loaded:", first.candidate_weights_loaded)
    print("training executed by evoagent:", first.training_executed_by_evoagent)
    print("external execution performed:", first.external_execution_performed)
