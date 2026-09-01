"""Verify and publish a privacy-bounded SEAGym + Terminal-Bench pilot bundle.

The verifier consumes the exact preregistered protocol and a completed SEAGym
run.  It recomputes the A0/AT comparison from trial-level records, checks the
underlying Harbor result and EvoAgent attestations, and emits only bounded
observable evidence.  Raw prompts, model text, trajectories, errors, and logs
never enter the publishable bundle.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Iterable
from uuid import UUID


FORMAT_VERSION = "evoagent-seagym-terminalbench-result-v1"
CLAIM = "real_seagym_terminalbench_subset_pilot_not_leaderboard"
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSONL_BYTES = 32 * 1024 * 1024
MAX_RECORDS = 10_000
FAILURE_RECEIPT_FILENAME = "evoagent-runtime-failure.json"
FAILURE_RECEIPT_SCHEMA = "evoagent-runtime-failure-v1"
NO_USABLE_ATIF_SKIP_CODE = "no_usable_harbor_atif_evidence"
FAILURE_RECEIPT_CLASSES = {
    "mimocode_process_failed",
    "runtime_sanitization_failed",
    "mimocode_and_sanitization_failed",
}
FAILURE_RECEIPT_STAGES = {"mimocode", "sanitize"}
MIMOCODE_EXIT_CLASSES = {"nonzero", "signal", "timeout", "spawn_failed", "success", "unknown"}
EXPECTED_MODEL_API = "xiaomi/mimo-v2.5"
EXPECTED_MODEL_HARBOR = "openrouter/xiaomi/mimo-v2.5"
EXPECTED_CANONICAL_MODEL = "xiaomi/mimo-v2.5-20260422"
EXPECTED_PROVIDER = "Xiaomi"
EXPECTED_ENDPOINT = "xiaomi/fp8"
EXPECTED_HARBOR_AGENT_INFO = {
    "name": "evoagent-mimo",
    "version": "0.1.0",
    "model_info": {"name": "xiaomi/mimo-v2.5", "provider": "openrouter"},
}
EXPECTED_TRIAL_RESULT_KEYS = {
    "id",
    "task_name",
    "trial_name",
    "trial_uri",
    "task_id",
    "source",
    "task_checksum",
    "config",
    "agent_info",
    "agent_result",
    "verifier_result",
    "exception_info",
    "started_at",
    "finished_at",
    "environment_setup",
    "agent_setup",
    "agent_execution",
    "verifier",
    "step_results",
}
EXPECTED_TRIAL_CONFIG_KEYS = {
    "task",
    "trial_name",
    "trials_dir",
    "install_only",
    "timeout_multiplier",
    "agent_timeout_multiplier",
    "verifier_timeout_multiplier",
    "agent_setup_timeout_multiplier",
    "environment_build_timeout_multiplier",
    "agent",
    "environment",
    "verifier",
    "artifacts",
    "extra_instruction_paths",
    "job_id",
}
EXPECTED_AGENT_CONTEXT_KEYS = {
    "n_input_tokens",
    "n_cache_tokens",
    "n_output_tokens",
    "cost_usd",
    "rollout_details",
    "metadata",
}
EXPECTED_TIMING_KEYS = {"started_at", "finished_at"}
HARBOR_SUPPORT_DIR_NAMES = {"_patched_tasksets"}
EXPECTED_PROTOCOL_ID = "evoagent-seagym-terminalbench2-mimo-v2.5-seed42-v10"
EXPECTED_AMENDMENT = {
    "amended_at": "2026-09-01T18:06:10Z",
    "benchmark_effect_claimed": False,
    "change": "synchronize_committed_candidate_with_seagym_live_state_and_bind_atif_identity",
    "diagnostic": {
        "artifact_id": 9813372700,
        "artifact_zip_sha256": "d2e5a12ddd704f350b3894a2b0483c29515a41db9ba4e01045d21fe982269c60",
        "completed_singleton_jobs": 12,
        "controller_commit": "b3375d7e02860d5fb6e391238f67a907f2f360d2",
        "evoagent_public_commit": "0217429e776e60c005396e26c9903c815c711ce0",
        "first_update": {"changed": True, "status": "updated"},
        "full_pilot_started": True,
        "job_id": 99953749409,
        "job_log_sha256": "fd9d1ba7fb281c764aa4be17ab2504649e6c046c1641f88ddc70f679e9e85215",
        "lifecycle_passed": True,
        "observed_key_usage_delta_usd": 0.035118395,
        "planned_singleton_jobs": 24,
        "proxy": {
            "completed_requests": 124,
            "final_upstream_http_4xx_errors": 0,
            "final_upstream_http_5xx_errors": 0,
            "forwarded_requests": 124,
            "rejected_requests": 0,
            "root_sessions_observed": 12,
            "upstream_attempts": 124,
            "upstream_errors": 0,
            "upstream_retries": 0,
        },
        "run_id": 33537027914,
        "score_produced": False,
        "second_update_error_code": "harbor_failure_receipt_snapshot_drifted",
        "status": "stale_live_baseline_state",
    },
    "evidence_contract_change": {
        "atif_agent_and_root_identity_must_match": True,
        "atif_component_hashes_must_match_current_candidate": True,
        "atif_model_route_seed_and_runtime_must_match": True,
        "atif_snapshot_must_match_current_candidate": True,
        "failure_receipt_identity_checks_relaxed": False,
        "stale_atif_may_update_agent": False,
        "stale_failure_receipt_may_update_agent": False,
    },
    "execution_change": {
        "committed_candidate_is_published_to_long_lived_baseline_state": True,
        "harbor_cli_retries_added": 0,
        "one_task_per_harbor_job": True,
        "planned_slot_replacement_allowed": False,
        "receipt_synthesis_allowed": False,
        "stale_live_state_is_rejected_fail_closed": True,
        "task_attempts_changed": False,
        "update_schedule_changed": False,
    },
    "frozen_scientific_identity": {
        "budgets_changed": False,
        "metrics_changed": False,
        "model_or_provider_changed": False,
        "new_seagym_config_sha256": "d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87",
        "prior_seagym_config_sha256": "d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87",
        "seed_or_order_changed": False,
        "tasks_or_split_changed": False,
    },
    "leaf_cause_status": {
        "raw_failure_receipt_persisted": False,
        "receipt_mismatch_direction_directly_observed": False,
        "runtime_state_propagation_bug_reproduced_offline": True,
        "source_confirmed_mechanism": "accepted_update_changed_internal_candidate_and_disk_state_but_did_not_refresh_seagym_long_lived_baseline_state_metadata_used_by_the_next_rollout",
        "triggering_exception_confirmed": True,
    },
    "prior_complete_comparisons": 0,
    "prior_controller_attempts_total": 16,
    "prior_observed_usage_delta_usd": 0.341499816,
    "prior_protocol_id": "evoagent-seagym-terminalbench2-mimo-v2.5-seed42-v9",
    "score_blind": True,
}
EXPECTED_PRIOR_AMENDMENT_V9 = {
    "amended_at": "2026-09-01T14:42:59Z",
    "benchmark_effect_claimed": False,
    "change": "isolate_each_planned_task_in_one_harbor_job",
    "diagnostic": {
        "artifact_id": 9804763525,
        "artifact_zip_sha256": "4eeef84ae962913fdf91e842cba5b7282a933cf17f55fc9ef663bc05e6c8c756",
        "controller_commit": "82f6ecc7f80961364d8ff6a233d344568b9a372b",
        "evoagent_public_commit": "030179e57b012afa3d96623ee874292bc7128f2e",
        "full_pilot_started": True,
        "harbor_partial_jobs": [
            {
                "completed": 2,
                "pending": 1,
                "returncode": 1,
                "trial_results": 2,
                "view": "replay",
            },
            {
                "completed": 1,
                "pending": 2,
                "returncode": 1,
                "trial_results": 1,
                "view": "train",
            },
        ],
        "job_id": 99886869291,
        "job_log_sha256": "933de87050003f8b0922408a1d55525164fec2e6eff0be9f2a3a2c19d2165e2d",
        "lifecycle_passed": True,
        "observed_key_usage_delta_usd": 0.028482794,
        "proxy": {
            "completed_requests": 87,
            "forwarded_requests": 87,
            "rejected_requests": 0,
            "root_sessions_observed": 9,
            "upstream_errors": 0,
            "upstream_retries": 0,
        },
        "run_id": 33517129366,
        "score_produced": False,
        "status": "incomplete_harbor_orchestration",
    },
    "execution_change": {
        "harbor_cli_retries_added": 0,
        "logical_batch_size_changed": False,
        "one_task_per_harbor_job": True,
        "planned_slot_replacement_allowed": False,
        "preserve_task_order": True,
        "receipt_synthesis_allowed": False,
        "task_attempts_changed": False,
        "update_schedule_changed": False,
    },
    "frozen_scientific_identity": {
        "budgets_changed": False,
        "metrics_changed": False,
        "model_or_provider_changed": False,
        "new_seagym_config_sha256": "d59f0f40f0d6d7f41606be77dba7cf10c91fde7cdd13683a8b3047cc7871ae87",
        "prior_seagym_config_sha256": "28f4c9078b36c78abdb72e31014629f47943f1bee1c2f94168004d62d8b0b195",
        "seed_or_order_changed": False,
        "tasks_or_split_changed": False,
    },
    "incomplete_evidence_policy": {
        "missing_real_child_result_is_model_failure": False,
        "missing_real_child_result_is_scoreable": False,
        "missing_real_child_result_may_update_agent": False,
        "required_complete_unique_single_task_jobs": 24,
    },
    "intervening_non_scoreable_runs": [
        {
            "artifact_id": 9800931910,
            "artifact_zip_sha256": "d40904e407b17ec30cbc29191f19b3590b200dcea5c4fe43fdb4921a966a2554",
            "controller_commit": "9711454825a8d67a92ea1a805256fdbfbaa666c8",
            "evoagent_public_commit": "030179e57b012afa3d96623ee874292bc7128f2e",
            "full_pilot_started": False,
            "job_id": 99857129389,
            "job_log_sha256": "ebbf8c3e0444b765459130f3ec44255715cb0dee2f47cb41e7c8733a850eb433",
            "lifecycle_child_exit_code": 0,
            "lifecycle_reason_code": "harbor_trial_contract_invalid",
            "observed_key_usage_delta_usd": 0.005162355,
            "proxy": {
                "completed_requests": 12,
                "forwarded_requests": 12,
                "rejected_requests": 0,
                "root_sessions_observed": 1,
                "upstream_attempts": 12,
                "upstream_errors": 0,
                "upstream_retries": 0,
            },
            "run_id": 33508167549,
            "score_produced": False,
            "status": "invalid_lifecycle_evidence",
        }
    ],
    "leaf_cause_status": {
        "source_supported_mechanism": "an_unhandled_per_trial_or_finalization_hook_exception_can_escape_harbor_taskgroup_and_cancel_pending_siblings; this matches but does not identify the observed leaf trigger",
        "raw_harbor_error_content_persisted": False,
        "triggering_exception_stage_or_type_confirmed": False,
    },
    "prior_complete_comparisons": 0,
    "prior_controller_attempts_total": 15,
    "prior_observed_usage_delta_usd": 0.306381421,
    "prior_protocol_id": "evoagent-seagym-terminalbench2-mimo-v2.5-seed42-v8",
    "score_blind": True,
}
EXPECTED_PRIOR_AMENDMENT_V8 = {
    "amended_at": "2026-08-30T10:04:18Z",
    "benchmark_effect_claimed": False,
    "change": "isolate_mimocode_root_session_model_call_surface",
    "diagnostic": {
        "artifact_id": 9730177730,
        "artifact_zip_sha256": "819d535de948bc3a8d2ecda62af647901d4fa4f82b309ac130a250930126b0bb",
        "controller_commit": "cc54328af922aed15093687f654383c2cf88f5e5",
        "evoagent_public_commit": "25ee0721f7d206b6168a6d7d642bebb1700d9b41",
        "job_id": 99239368341,
        "job_log_sha256": "217028db03c83c93e90577aceea539825babeea93a18deb5c0ea28def13f7051",
        "lifecycle_budget_guard_delta_usd": 0.004855739,
        "observed_key_usage_delta_usd": 0.005334952,
        "proxy": {
            "completed_requests": 12,
            "forwarded_requests": 12,
            "inbound_tool_choice_absent": 1,
            "inbound_tool_choice_auto": 11,
            "rejected_requests": 0,
            "upstream_attempts": 12,
            "upstream_errors": 0,
            "upstream_retries": 0,
        },
        "run_id": 33304816856,
        "score_produced": False,
        "status": "invalid_evidence",
    },
    "evidence_boundary": {
        "exact_atif_model_call_count_available": False,
        "inference": "one_unattested_auxiliary_title_request",
        "inference_confidence": "high",
        "raw_event_or_response_content_persisted": False,
        "support": "one_absent_tool_choice_request_plus_eleven_auto_requests_and_mimocode_v0.1.13_title_source_contract",
    },
    "frozen_scientific_identity": {
        "budgets_changed": False,
        "metrics_changed": False,
        "model_or_provider_changed": False,
        "seagym_config_sha256": "28f4c9078b36c78abdb72e31014629f47943f1bee1c2f94168004d62d8b0b195",
        "seed_or_order_changed": False,
        "tasks_or_split_changed": False,
    },
    "generated_runtime_config_change": {
        "actor_subsessions_enabled": False,
        "automatic_checkpoint_enabled": False,
        "automatic_cron_enabled": False,
        "automatic_distill_enabled": False,
        "automatic_dream_enabled": False,
        "disposable_home_bound": True,
        "fixed_session_title": "evoagent-seagym-trial",
        "inherited_config_overlay_cleared": True,
        "mcp_sampling_enabled": False,
        "next_prompt_prediction_enabled": False,
        "proxy_session_affinity_required": True,
        "proxy_to_atif_model_call_equality_required": True,
        "pure_mode_enabled": True,
        "root_compaction_enabled": True,
        "small_model_route_changed": False,
        "task_scoped_rollout_requests_only": True,
        "title_agent_enabled": False,
    },
    "prior_complete_comparisons": 0,
    "prior_controller_attempts_total": 13,
    "prior_observed_usage_delta_usd": 0.272736272,
    "prior_protocol_id": "evoagent-seagym-terminalbench2-mimo-v2.5-seed42-v7",
    "score_blind": True,
}
EXPECTED_PRIOR_AMENDMENT_V7 = {
    "adapter_evidence_change": "accept_only_bounded_numeric_reasoning_token_usage_telemetry_while_rejecting_reasoning_content_and_require_hash_bound_failure_receipts_for_errored_trials",
    "amended_at": "2026-08-30T06:39:29Z",
    "compatibility_normalization": {
        "applies_only_when": "inbound_tool_choice_none_with_nonempty_local_function_tools",
        "benchmark_effect_claimed": False,
        "inbound_semantics": "tool_calls_disabled_for_the_final_text_response",
        "model_provider_tasks_seed_or_config_changed": False,
        "outbound_change": "delete_tool_choice_and_tools_before_forwarding",
        "outbound_semantics": "no_local_function_tools_are_offered_to_the_model",
        "retry_identity": "byte_identical_normalized_outbound_body",
        "unexpected_outbound_tool_call_policy": "reject_response_and_make_pilot_incomplete",
    },
    "config_sha256_unchanged": "28f4c9078b36c78abdb72e31014629f47943f1bee1c2f94168004d62d8b0b195",
    "diagnostic_artifact_digest_kind": "github_actions_artifact_zip_sha256",
    "diagnostic_artifact_id": 9727486245,
    "diagnostic_artifact_sha256": "84dcd2eb4a08a24144e50290afc5aacb373ce4f1adb30703d5cd7e3ea79a53c9",
    "diagnostic_controller_run_id": 33295415122,
    "diagnostic_job_log": {
        "digest_kind": "github_actions_job_log_download_raw_bytes_sha256",
        "job_id": 99214123678,
        "safe_fixed_phrase": "train batch contains no usable Harbor ATIF evidence",
        "safe_fixed_phrase_occurrences": 2,
        "sha256": "b02d75dfe3e7591af55577c917185b55823eedca6590f52de7eda9911310f181",
    },
    "diagnostic_observation": {
        "completed_requests": 111,
        "final_upstream_http_404_errors": 0,
        "forwarded_requests": 111,
        "rejected_requests": 0,
        "tool_choice_none_normalizations": 7,
        "train_batches_without_usable_atif": 1,
        "upstream_attempts": 111,
        "upstream_http_404_attempts": 0,
        "upstream_other_attempt_errors": 0,
        "upstream_retries": 0,
    },
    "execution_resilience_change": "classify_mimocode_and_sanitizer_failures_before_harbor_post_run_recovery_and_skip_an_update_only_when_every_missing_atif_has_a_valid_content_free_hash_bound_failure_receipt",
    "leaf_cause_status": {
        "exact_failed_run_reasoning_token_value_available": False,
        "highest_probability_hypothesis": "provider_reported_reasoning_token_usage_was_rejected_as_reasoning_content_by_the_frozen_runtime_sanitizer",
        "hypothesis_confirmed_for_run_33295415122": False,
        "raw_event_or_response_content_persisted": False,
    },
    "prior_complete_comparisons": 0,
    "prior_controller_attempts_total": 12,
    "prior_pre_v2_controller_attempts": 4,
    "prior_v2_controller_attempts": 4,
    "prior_v3_controller_attempts": 1,
    "prior_v4_controller_attempts": 1,
    "prior_v5_controller_attempts": 1,
    "prior_v6_controller_attempts": 1,
    "prior_model_inference_completed": True,
    "prior_observed_usage_delta_usd": 0.26740132,
    "prior_protocol_id": "evoagent-seagym-terminalbench2-mimo-v2.5-seed42-v6",
    "prior_v2_run_evidence": [
        {
            "artifact_id": 9710915384,
            "artifact_sha256": "911253517539b6bfb0d851c0362d8de070eef21655e3d09e4da96991295f157c",
            "controller_commit": "c79bc1eae30c30c462cf034a8b355199ddb5f31f",
            "observed_usage_delta_usd": 0.000395987,
            "run_id": 33239134974,
            "score_produced": False,
        },
        {
            "artifact_id": 9715191473,
            "artifact_sha256": "a251cbde6595868e94e113d9bf1a3867ec0b3251e07806cc3a37bfefffebeb15",
            "controller_commit": "7f0088cfea552cbd09fc0c124b1d3d01acf36feb",
            "observed_usage_delta_usd": 0.002472451,
            "run_id": 33253565374,
            "score_produced": False,
        },
        {
            "artifact_id": 9716114523,
            "artifact_sha256": "47545121eacca279e25ff890a6dfc3d33832490955abb1ac0853daddddd93e9f",
            "controller_commit": "a6039f08e8931ee6e51b56e21e1146a623e834df",
            "observed_usage_delta_usd": 0.043002461,
            "run_id": 33256278875,
            "score_produced": False,
        },
        {
            "artifact_id": 9716899456,
            "artifact_sha256": "512cdedd6a0b4100e6fd2bf20bd1414c8cfeca451988e919d7149d2db28a5145",
            "controller_commit": "d896cae72135b43ff204b2fb29914deede8b9d0b",
            "observed_usage_delta_usd": 0.0446062,
            "run_id": 33259140059,
            "score_produced": False,
        },
    ],
    "prior_v3_run_evidence": [
        {
            "artifact_id": 9718565501,
            "artifact_sha256": "25d933194a193b5b2c0a6f35049089c5dd7f70abeb9581600368ab9c960fe4ec",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "9524dfbf786492d3fbeed27791bff4cbac280112",
            "observed_usage_delta_usd": 0.041496039,
            "run_id": 33265128690,
            "score_produced": False,
        }
    ],
    "prior_v4_run_evidence": [
        {
            "artifact_id": 9724445260,
            "artifact_sha256": "9b4d9465991ed5f9ef0bb5db5a3d56253751289917878b0a39a18f8e2359caee",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "9c25f473e8054bac76d7128f0ac025dd3e154080",
            "evoagent_public_commit": "09018d7b4bdfcdc11f61f8c302c857d7f5dfd7f7",
            "observed_usage_delta_usd": 0.042464641,
            "run_id": 33285475794,
            "score_produced": False,
        }
    ],
    "prior_v5_run_evidence": [
        {
            "artifact_id": 9725879182,
            "artifact_sha256": "d89cee0d82b57881d721179decf4bc06282adf6d77a511b3fd085469c0f3fa54",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "8c2015febaabca2710e90805bb9ee95e0ec5a83c",
            "evoagent_public_commit": "092ba665fd01450ee70446fe3f8e30cce4775c08",
            "observed_usage_delta_usd": 0.042668012,
            "run_id": 33289924348,
            "score_produced": False,
        }
    ],
    "prior_v6_run_evidence": [
        {
            "artifact_id": 9727486245,
            "artifact_sha256": "84dcd2eb4a08a24144e50290afc5aacb373ce4f1adb30703d5cd7e3ea79a53c9",
            "blocker_code": "seagym_execution_failed",
            "controller_commit": "2a44abedde490fc3d6d602a372284db030357eb4",
            "evoagent_public_commit": "9889fee8888baca681311a3c10880a7144f5736d",
            "job_id": 99214123678,
            "job_log_sha256": "b02d75dfe3e7591af55577c917185b55823eedca6590f52de7eda9911310f181",
            "observed_usage_delta_usd": 0.050295529,
            "run_id": 33295415122,
            "score_produced": False,
        }
    ],
    "prior_score_produced": False,
    "reason_code": "harbor_post_run_missing_atif_masked_an_inner_mimocode_or_runtime_sanitizer_failure",
    "root_cause_evidence": {
        "benchmark_result_claimed": False,
        "confidence": "high_for_two_layer_failure_and_unconfirmed_for_exact_leaf_cause",
        "frozen_mimocode_local_capture": {
            "capture_content_persisted": False,
            "observable_contract": "completion_tokens_details_reasoning_tokens_maps_to_step_finish_part_tokens_reasoning",
            "runtime": "mimocode-v0.1.13",
        },
        "harbor_recovery_contract": {
            "classified_nonzero_agent_failure_is_contained": True,
            "generic_runtime_error_can_be_masked_by_a_second_missing_atif_error": True,
        },
        "run_observation": {
            "completed_requests": 111,
            "proxy_or_upstream_errors": 0,
            "train_batch_without_usable_atif": True,
        },
    },
    "score_blind": True,
    "transport_only_change": False,
}
EXPECTED_RETRY_POLICY = {
    "ambiguous_transport_failures_retried": False,
    "backoff_seconds": [5.0, 10.0, 20.0, 40.0],
    "fallbacks_enabled": False,
    "max_retries_per_client_request": 4,
    "request_body_changed_between_attempts": False,
    "retryable_http_statuses": [404, 408, 409, 425, 429, 500, 502, 503, 504, 524, 529],
    "same_model_provider_endpoint": True,
}
EXPECTED_GUARD_PROXY_RUNTIME = {
    "health_schema_version": "openrouter-guard-proxy-health-v5",
    "limits": {
        "client_timeout_seconds": 30.0,
        "max_concurrency": 2,
        "max_output_tokens": 16_000,
        "max_request_bytes": 2 * 1024 * 1024,
        "max_requests": 768,
        "max_response_bytes": 16 * 1024 * 1024,
        "upstream_timeout_seconds": 300.0,
    },
    "root_session_binding": {
        "enabled": True,
        "full_pilot_limit": 24,
        "header": "x-session-affinity",
        "health_schema_version": "openrouter-guard-proxy-health-v5",
        "lifecycle_canary_limit": 1,
        "parent_header_forbidden": "x-parent-session-id",
        "payload_binding": "prompt_cache_key",
        "route_canary_limit": 1,
    },
    "source_sha256": "e2cea221758f09c8658a65e120be3056d4dc5948eccb93668c3e3561d363fe29",
    "telemetry": {
        "normalization_counters": ["tool_choice_none_to_no_tools"],
        "raw_request_content_persisted": False,
        "request_profile_buckets": ["absent", "auto", "required", "none", "named"],
        "request_profile_fields": [
            "inbound_tool_choice",
            "outbound_tool_choice",
            "final_upstream_errors_by_outbound_tool_choice",
        ],
    },
}
PROXY_TOOL_CHOICE_BUCKETS = ("absent", "auto", "required", "none", "named")
EXPECTED_CLAIM_BOUNDARY = {
    "automatic_promotion": False,
    "causal_attribution_claimed": False,
    "leaderboard_submission": False,
    "paper_scale_reproduction": False,
    "pilot_kind": "real_external_scientific_pilot",
    "results_status": "preregistered_incomplete_attempts_no_score",
}
PROXY_ERROR_CLASSES = (
    "http_4xx",
    "http_5xx",
    "timeout",
    "unavailable",
    "response_too_large",
    "identity_invalid",
    "other",
)
PROXY_HTTP_STATUS_BUCKETS = (
    "400",
    "401",
    "402",
    "403",
    "404",
    "408",
    "409",
    "413",
    "422",
    "425",
    "429",
    "500",
    "502",
    "503",
    "504",
    "524",
    "529",
    "other_4xx",
    "other_5xx",
    "other",
)
EXPECTED_ROUTE_CONTRACT = {
    "provider": {
        "only": [EXPECTED_ENDPOINT],
        "allow_fallbacks": False,
        "require_parameters": True,
    },
    "reasoning": {"enabled": False},
    "accepted_response_models": [EXPECTED_MODEL_API, EXPECTED_CANONICAL_MODEL],
    "response_provider": EXPECTED_PROVIDER,
}
EXPECTED_ROUTE_CONTRACT_SHA256 = hashlib.sha256(
    json.dumps(
        EXPECTED_ROUTE_CONTRACT,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
).hexdigest()
EXPECTED_ADAPTER_VERSION = "0.1.0"
EXPECTED_UPSTREAM = {
    "seagym": "9e61e14db1f1355de944cd7c5b10c244fc74e82d",
    "harbor_seagym_gitlink": "f7110f1a240c6a50589b90c4d69714763946d088",
    "terminal_bench_2": "2fd12b88aafdd04a52c298e3940bcb189f9766d6",
}
EXPECTED_MIMOCODE = {
    "version": "0.1.13",
    "commit": "67c9cf1e26288d03c65fb844be71f39581ffc1de",
    "asset_sha256": "0997a43647a99969d0194fad71af1fd6112aa8220e24a4562aea63953b1e1ada",
    "auxiliary_model_calls": {
        "actor_subsessions_enabled": False,
        "automatic_checkpoint_enabled": False,
        "automatic_cron_enabled": False,
        "automatic_distill_enabled": False,
        "automatic_dream_enabled": False,
        "mcp_sampling_enabled": False,
        "next_prompt_prediction_enabled": False,
        "title_agent_enabled": False,
        "unattested_model_calls_allowed": False,
    },
    "execution_isolation": {
        "build_tool_allowlist": ["bash", "read", "write", "edit", "glob", "grep"],
        "compaction_auto_enabled": True,
        "config_content_overlay": "{}",
        "disposable_home_environment": ["HOME", "MIMOCODE_HOME", "USERPROFILE"],
        "fixed_session_title": "evoagent-seagym-trial",
        "mcp_servers_configured": False,
        "proxy_session_affinity_header": "x-session-affinity",
        "proxy_session_affinity_required": True,
        "pure_mode_enabled": True,
        "root_session_only": True,
    },
}
SECRET_PATTERNS = (
    re.compile(r"sk-or-v1-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_TOOL_NAMES = {
    "apply_patch",
    "bash",
    "browser",
    "edit",
    "edit_file",
    "exec",
    "exec_command",
    "execute",
    "execute_command",
    "glob",
    "grep",
    "python",
    "read",
    "read_file",
    "search",
    "shell",
    "web_search",
    "websearch",
    "webfetch",
    "codesearch",
    "actor",
    "skill",
    "write",
    "write_file",
}


class VerificationError(ValueError):
    pass


def _reject_constant(value: str) -> None:
    raise VerificationError(f"non-finite JSON number is forbidden: {value}")


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError("duplicate JSON object key")
        result[key] = value
    return result


def _strict_loads(data: str | bytes) -> Any:
    try:
        return json.loads(
            data,
            parse_constant=_reject_constant,
            object_pairs_hook=_no_duplicates,
        )
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError) as exc:
        raise VerificationError("invalid bounded UTF-8 JSON") from exc


def _regular_file(path: Path, *, root: Path | None = None, max_bytes: int = MAX_JSON_BYTES) -> Path:
    lexical = path.absolute()
    for part in (lexical, *lexical.parents):
        if part.exists() and _is_linklike(part):
            raise VerificationError(f"symlinked evidence path is forbidden: {path}")
        if root is not None and part == root.absolute():
            break
    if root is not None:
        try:
            root = root.resolve(strict=True)
            candidate = path if path.is_absolute() else root / path
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise VerificationError(f"expected evidence file is missing: {path}") from exc
        if resolved != root and root not in resolved.parents:
            raise VerificationError(f"path escapes controlled root: {path}")
    else:
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise VerificationError(f"expected evidence file is missing: {path}") from exc
    if _is_linklike(resolved) or not resolved.is_file():
        raise VerificationError(f"expected regular non-symlink file: {path}")
    if resolved.stat().st_size > max_bytes:
        raise VerificationError(f"file exceeds size limit: {path}")
    return resolved


def _is_linklike(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction and is_junction())


def _load_json(path: Path, *, root: Path | None = None, max_bytes: int = MAX_JSON_BYTES) -> Any:
    controlled = _regular_file(path, root=root, max_bytes=max_bytes)
    return _strict_loads(controlled.read_bytes())


def _load_jsonl(path: Path, *, root: Path, expected: int | None = None) -> list[dict[str, Any]]:
    controlled = _regular_file(path, root=root, max_bytes=MAX_JSONL_BYTES)
    records: list[dict[str, Any]] = []
    with controlled.open("rb") as handle:
        for line in handle:
            if not line.strip():
                raise VerificationError(f"blank JSONL record in {path.name}")
            if len(line) > MAX_JSON_BYTES:
                raise VerificationError(f"oversized JSONL record in {path.name}")
            value = _strict_loads(line)
            if not isinstance(value, dict):
                raise VerificationError(f"JSONL record is not an object in {path.name}")
            records.append(value)
            if len(records) > MAX_RECORDS:
                raise VerificationError(f"too many JSONL records in {path.name}")
    if expected is not None and len(records) != expected:
        raise VerificationError(f"{path.name} expected {expected} records, found {len(records)}")
    return records


def _sha256(path: Path, *, max_bytes: int = MAX_JSONL_BYTES) -> str:
    controlled = _regular_file(path, max_bytes=max_bytes)
    digest = hashlib.sha256()
    with controlled.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_git_text(path: Path) -> str:
    """Hash text with Git's repository LF normalization on every host."""

    raw = _regular_file(path, max_bytes=MAX_JSONL_BYTES).read_bytes()
    if b"\x00" in raw:
        raise VerificationError("expected text artifact contains NUL bytes")
    normalized = raw.replace(b"\r\n", b"\n")
    if b"\r" in normalized:
        raise VerificationError("text artifact contains a bare carriage return")
    return hashlib.sha256(normalized).hexdigest()


def _canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _finite_number(value: Any, label: str, *, minimum: float = 0.0, maximum: float = 1e15) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise VerificationError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise VerificationError(f"{label} is outside its bounded range")
    return number


def _bounded_int(value: Any, label: str, *, minimum: int = 0, maximum: int = 10**12) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise VerificationError(f"{label} must be a bounded integer")
    return value


def _scan_secret_bytes(path: Path) -> None:
    raw = _regular_file(path, max_bytes=MAX_JSONL_BYTES).read_text(encoding="utf-8")
    if any(pattern.search(raw) for pattern in SECRET_PATTERNS):
        raise VerificationError(f"credential-like material detected in {path.name}")


def _reject_nonempty_reasoning(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = key.lower().replace("-", "_")
            if normalized in {"reasoning", "reasoning_content", "reasoning_details", "chain_of_thought", "scratchpad"}:
                if item not in (None, "", [], {}):
                    raise VerificationError("non-empty hidden reasoning was persisted")
            _reject_nonempty_reasoning(item)
    elif isinstance(value, list):
        for item in value:
            _reject_nonempty_reasoning(item)


def _repo_root(protocol_path: Path) -> Path:
    resolved = protocol_path.resolve(strict=True)
    if resolved.parent.name != "seagym_terminalbench" or resolved.parent.parent.name != "experiments":
        raise VerificationError("protocol must be inside experiments/seagym_terminalbench")
    return resolved.parents[2]


def _validate_protocol(protocol_path: Path) -> tuple[dict[str, Any], Path]:
    protocol = _load_json(protocol_path)
    if not isinstance(protocol, dict) or protocol.get("format_version") != "evoagent-seagym-terminalbench-pilot-v1":
        raise VerificationError("protocol identity is invalid")
    if protocol.get("protocol_id") != EXPECTED_PROTOCOL_ID or protocol.get("amendment") != EXPECTED_AMENDMENT:
        raise VerificationError("score-blind protocol amendment drifted")
    if protocol.get("prior_amendment_v9") != EXPECTED_PRIOR_AMENDMENT_V9:
        raise VerificationError("preserved v9 protocol amendment drifted")
    if protocol.get("prior_amendment_v8") != EXPECTED_PRIOR_AMENDMENT_V8:
        raise VerificationError("preserved v8 protocol amendment drifted")
    if protocol.get("prior_amendment_v7") != EXPECTED_PRIOR_AMENDMENT_V7:
        raise VerificationError("preserved v7 protocol amendment drifted")
    repo_root = _repo_root(protocol_path)
    if protocol.get("upstream") != EXPECTED_UPSTREAM:
        raise VerificationError("upstream commit lock drifted")
    schedule = protocol.get("schedule")
    expected_schedule = {
        "seed": 42,
        "train_size": 6,
        "val_size": 3,
        "test_size": 3,
        "batch_size": 3,
        "num_epochs": 1,
        "num_train_batches": 2,
        "num_updates": 2,
        "num_updates_per_batch": 1,
        "shuffle_train": False,
        "frozen_manifest_order": True,
    }
    if not isinstance(schedule, dict) or any(schedule.get(key) != value for key, value in expected_schedule.items()):
        raise VerificationError("protocol schedule drifted")
    planned = schedule.get("expected_task_trials")
    if not isinstance(planned, dict) or planned.get("total") != 24:
        raise VerificationError("protocol task-trial count drifted")
    resources = protocol.get("resources")
    expected_budget_guard = {
        "accounting_scope": "entire_openrouter_key_between_before_and_after_checks",
        "command_timeout_seconds": 13200,
        "poll_seconds": 10,
        "stop_threshold_usd": 0.9,
        "usage_check_failures_before_stop": 3,
    }
    if (
        not isinstance(resources, dict)
        or resources.get("authorized_max_observed_key_usage_delta_usd") != 1.2
        or resources.get("budget_guard") != expected_budget_guard
        or resources.get("harbor_concurrency") != 1
        or resources.get("harbor_job_isolation")
        != {
            "expected_unique_jobs": 24,
            "one_task_per_job": True,
            "retries": 0,
            "synthetic_failure_receipts": False,
        }
        or resources.get("mimocode_force_kill_grace_seconds") != 15
        or resources.get("mimocode_route_canary_timeout_seconds") != 600
        or resources.get("mimocode_sanitization_margin_seconds") != 120
        or resources.get("non_full_pilot_workflow_reserve_seconds") != 5100
    ):
        raise VerificationError("protocol resource or budget guard drifted")
    if resources.get("lifecycle_canary") != {
        "budget_stop_threshold_usd": 0.15,
        "command_timeout_seconds": 2400,
        "must_complete_before_full_pilot": True,
        "purpose": "integration_only_no_benchmark_score",
        "seed": 42,
        "task_id": "terminal-bench/fix-git",
    }:
        raise VerificationError("protocol lifecycle canary drifted")
    route = protocol.get("model_route")
    if not isinstance(route, dict):
        raise VerificationError("protocol route is missing")
    expected_route_fields = {
        "request_model": EXPECTED_MODEL_API,
        "harbor_model": EXPECTED_MODEL_HARBOR,
        "same_model_for_update_and_rollout": True,
        "provider_rollout_sampling_determinism_claimed": False,
    }
    if any(route.get(key) != value for key, value in expected_route_fields.items()):
        raise VerificationError("protocol model route drifted")
    if route.get("route_contract") != EXPECTED_ROUTE_CONTRACT:
        raise VerificationError("protocol strict route contract drifted")
    if route.get("router_audit") != {
        "accepted_strategies": ["alias", "direct"],
        "cache_enabled": False,
        "material_pipeline_stages_allowed": False,
        "metadata_required": True,
        "successful_attempt": 1,
    }:
        raise VerificationError("protocol router-audit contract drifted")
    if route.get("reasoning_semantics") != {
        "absence_of_internal_reasoning_claimed": False,
        "reasoning_content_persisted": False,
        "reasoning_request_enabled": False,
        "safe_provider_usage_count_may_be_reported": True,
    }:
        raise VerificationError("protocol reasoning semantics drifted")
    claim = protocol.get("claim_boundary")
    if claim != EXPECTED_CLAIM_BOUNDARY:
        raise VerificationError("protocol claim boundary drifted")
    runtime = protocol.get("runtime") or {}
    mimocode = runtime.get("mimocode") or {}
    if any(mimocode.get(key) != value for key, value in EXPECTED_MIMOCODE.items()):
        raise VerificationError("MiMoCode runtime identity drifted")
    expected_credential_transport = {
        "account_key_in_task_container": False,
        "container_credential_kind": "ephemeral_proxy_capability",
        "kind": "host_guard_proxy",
        "proxy_base_url": "http://evoagent-openrouter-proxy:18765/api/v1",
    }
    if runtime.get("credential_transport") != expected_credential_transport:
        raise VerificationError("runtime credential transport drifted")
    if runtime.get("guard_proxy") != EXPECTED_GUARD_PROXY_RUNTIME:
        raise VerificationError("runtime guard-proxy identity drifted")
    if runtime.get("openrouter_retry_policy") != EXPECTED_RETRY_POLICY:
        raise VerificationError("runtime retry policy drifted")
    expected_privacy_sanitizer = {
        "raw_jsonl_max_bytes": 64 * 1024 * 1024,
        "raw_persisted": False,
        "raw_record_max_bytes": 16 * 1024 * 1024,
        "raw_string_max_chars": 16 * 1024 * 1024,
        "reasoning_content_persisted": False,
        "reasoning_token_count_telemetry_allowed": True,
    }
    if runtime.get("privacy_sanitizer") != expected_privacy_sanitizer:
        raise VerificationError("runtime privacy sanitizer bounds drifted")
    expected_token_semantics = {
        "harbor_cached_tokens": "cache_read_subset_of_harbor_input_tokens",
        "harbor_input_tokens": "non_cached_input_plus_cache_read",
        "harbor_reasoning_tokens": "provider_reported_usage_count_only_not_reasoning_content",
        "reported_attested_total_tokens": "harbor_input_tokens_plus_visible_output_tokens_plus_reasoning_tokens",
        "seagym_total_tokens": "harbor_input_tokens_plus_harbor_cached_tokens_plus_output_tokens",
    }
    if runtime.get("token_semantics") != expected_token_semantics:
        raise VerificationError("runtime token semantics drifted")
    expected_seed_semantics = {
        "controls": [
            "task_split",
            "frozen_batch_order",
            "update_attempt_record",
            "checkpoint_and_trial_attestation",
        ],
        "provider_update_sampling_determinism_claimed": False,
        "provider_rollout_sampling_determinism_claimed": False,
    }
    if schedule.get("seed_semantics") != expected_seed_semantics:
        raise VerificationError("protocol seed semantics drifted")
    if route.get("provider_update_sampling_determinism_claimed") is not False:
        raise VerificationError("provider update sampling claim drifted")
    if route.get("update_model_seed_parameter_sent") is not False:
        raise VerificationError("update model seed-parameter contract drifted")
    artifacts = protocol.get("artifacts")
    if not isinstance(artifacts, dict):
        raise VerificationError("protocol artifact lock is missing")
    for name in (
        "config",
        "split",
        "task_index",
        "seagym_redaction_patch",
        "seagym_job_isolation_patch",
    ):
        item = artifacts.get(name)
        if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not HEX64.fullmatch(str(item.get("sha256", ""))):
            raise VerificationError(f"invalid protocol artifact lock: {name}")
        actual = _sha256(_regular_file(repo_root / item["path"], root=repo_root))
        if actual != item["sha256"]:
            raise VerificationError(f"protocol artifact hash mismatch: {name}")
    isolation_patch = artifacts["seagym_job_isolation_patch"]
    if isolation_patch.get("targets") != [
        {
            "blob_sha": "a63073693ae1d39da914f251518e68615991916c",
            "path": "seagym/envs/harbor_env/env.py",
        },
        {
            "blob_sha": "2edf535f3d06210f35002ca27f883175bb547a6f",
            "path": "seagym/trainers/builder.py",
        },
        {
            "blob_sha": "7f5743412a8a4c60fe723ccc9eba6c05c8b2658b",
            "path": "tests/test_harbor_results.py",
        },
        {
            "blob_sha": "0a75953a67c0f34237005c0fa35632dbbf45ced8",
            "path": "tests/test_trainer_reports.py",
        },
    ]:
        raise VerificationError("SEAGym job-isolation patch target lock drifted")
    lock_hash = artifacts.get("third_party_lock_sha256")
    if not isinstance(lock_hash, str) or _sha256_git_text(repo_root / "THIRD_PARTY_LOCK.json") != lock_hash:
        raise VerificationError("THIRD_PARTY_LOCK hash mismatch")
    return protocol, repo_root


def _validate_frozen_inputs(protocol: dict[str, Any], repo_root: Path, run_dir: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    artifacts = protocol["artifacts"]
    config = _load_json(repo_root / artifacts["config"]["path"], root=repo_root)
    split = _load_json(repo_root / artifacts["split"]["path"], root=repo_root)
    task_index = _load_json(repo_root / artifacts["task_index"]["path"], root=repo_root)
    actual_config = _load_json(run_dir / "inputs" / "experiment_config.json", root=run_dir)
    actual_split = _load_json(run_dir / "inputs" / "split_manifest.json", root=run_dir)
    if actual_config != config:
        raise VerificationError("executed experiment config differs from the frozen config")
    if actual_split != split:
        raise VerificationError("executed split differs from the frozen split")
    if not isinstance(config, dict) or config.get("seed") != 42:
        raise VerificationError("frozen config seed drifted")
    backend_config = config.get("backend") or {}
    if (
        not isinstance(backend_config, dict)
        or backend_config.get("n_concurrent") != 1
        or backend_config.get("one_task_per_harbor_job") is not True
        or backend_config.get("n_concurrent")
        != protocol["resources"]["harbor_concurrency"]
    ):
        raise VerificationError("frozen Harbor concurrency drifted")
    baseline_config = ((config.get("baseline") or {}).get("config") or {})
    rollout_kwargs = (((config.get("rollout_agent") or {}).get("config") or {}).get("kwargs") or {})
    for value in (baseline_config, rollout_kwargs):
        if value.get("route_contract") != EXPECTED_ROUTE_CONTRACT:
            raise VerificationError("config route contract drifted")
        if value.get("seed") != 42:
            raise VerificationError("nested baseline or rollout seed drifted")
    if baseline_config.get("automatic_promotion") is not False or baseline_config.get("causal_attribution_claimed") is not False:
        raise VerificationError("baseline claim boundary drifted")
    if baseline_config.get("fail_on_update_error") is not True:
        raise VerificationError("paid pilot is not configured to fail closed on an update error")
    rollout_model = ((config.get("rollout_agent") or {}).get("models") or {}).get("rollout_model") or {}
    if (
        rollout_model.get("api_base") != "http://evoagent-openrouter-proxy:18765/api/v1"
        or rollout_model.get("api_key_env") != "EVOAGENT_MIMOCODE_PROXY_TOKEN"
        or (rollout_model.get("exports") or {}).get("OPENROUTER_API_KEY") != "{api_key}"
    ):
        raise VerificationError("rollout credential proxy binding drifted")
    tasks = task_index.get("tasks") if isinstance(task_index, dict) else None
    if not isinstance(tasks, list) or len(tasks) != 12:
        raise VerificationError("frozen task index must contain exactly 12 tasks")
    task_ids = [item.get("task_id") for item in tasks if isinstance(item, dict)]
    splits = split.get("splits") if isinstance(split, dict) else None
    if not isinstance(splits, dict) or task_ids != splits.get("train", []) + splits.get("val", []) + splits.get("test", []):
        raise VerificationError("task-index order differs from the frozen split")
    return config, split, task_index


def _validate_batch_plan(run_dir: Path, split: dict[str, Any]) -> dict[str, Any]:
    plan = _load_json(run_dir / "inputs" / "batch_plan.json", root=run_dir)
    if not isinstance(plan, dict) or plan.get("seed") != 42 or plan.get("split_id") != split.get("split_id"):
        raise VerificationError("executed batch plan identity drifted")
    splits = split["splits"]
    batches = plan.get("train_batches")
    if not isinstance(batches, list) or batches != [splits["train"][:3], splits["train"][3:]]:
        raise VerificationError("executed train batch order drifted")
    views = plan.get("views")
    if not isinstance(views, dict):
        raise VerificationError("batch-plan views are missing")
    if views.get("update_validation") != splits["val"]:
        raise VerificationError("update-validation task set drifted")
    final = views.get("final")
    if not isinstance(final, dict) or final != {"id_test": splits["test"]}:
        raise VerificationError("final held-out task set drifted")
    replay = views.get("replay")
    if not isinstance(replay, list) or len(replay) != 3 or len(set(replay)) != 3 or not set(replay) <= set(splits["train"]):
        raise VerificationError("replay task set violates the frozen contract")
    return plan


def _checkpoint_snapshot(
    run_dir: Path,
    checkpoint_id: str,
) -> tuple[str, str, dict[str, dict[str, str]]]:
    checkpoints_root = (run_dir / "checkpoints").resolve(strict=True)
    checkpoint_dir = _regular_file(
        checkpoints_root / checkpoint_id / "checkpoint.json",
        root=checkpoints_root,
    ).parent
    manifest = _load_json(checkpoint_dir / "checkpoint.json", root=checkpoints_root)
    baseline = manifest.get("baseline") if isinstance(manifest, dict) else None
    if not isinstance(baseline, dict) or baseline.get("schema_version") != "evoagent-seagym-checkpoint-v1":
        raise VerificationError(f"invalid EvoAgent checkpoint: {checkpoint_id}")
    state_ref = baseline.get("state_ref")
    if not isinstance(state_ref, str):
        raise VerificationError(f"checkpoint state_ref missing: {checkpoint_id}")
    state_dir = (checkpoint_dir / state_ref).resolve(strict=True)
    if state_dir != checkpoints_root and checkpoints_root not in state_dir.parents:
        raise VerificationError("checkpoint state escapes controlled root")
    if state_dir.is_symlink() or not state_dir.is_dir():
        raise VerificationError("checkpoint state is not a regular directory")
    inventory: dict[str, str] = {}
    for item in sorted(state_dir.rglob("*")):
        if item.is_symlink():
            raise VerificationError("checkpoint state contains a symlink")
        if item.is_dir():
            continue
        relative = item.relative_to(state_dir).as_posix()
        if not (
            relative == "state.json"
            or (relative.startswith("snapshots/") and relative.endswith(".json"))
            or (relative.startswith("attempts/") and relative.endswith(".json"))
            or (relative.startswith("prompts/") and relative.endswith(".md"))
        ):
            raise VerificationError("checkpoint state contains an unexpected file")
        inventory[relative] = _sha256(item, max_bytes=2 * 1024 * 1024)
    if baseline.get("state_inventory") != inventory or baseline.get("state_inventory_sha256") != _canonical_sha(inventory):
        raise VerificationError("checkpoint state inventory is invalid")
    state = _load_json(state_dir / "state.json", root=state_dir)
    if not isinstance(state, dict) or state.get("schema_version") != "evoagent-seagym-state-v1":
        raise VerificationError("checkpoint state manifest is invalid")
    if state.get("seed") != 42 or state.get("model_id") != EXPECTED_MODEL_API:
        raise VerificationError("checkpoint state model or seed drifted")
    if state.get("causal_attribution_claimed") is not False or state.get("promotion_claimed") is not False:
        raise VerificationError("checkpoint state claims causality or promotion")
    a0 = state.get("a0_sha256")
    candidate = state.get("evaluation_candidate_sha256")
    if not isinstance(a0, str) or not HEX64.fullmatch(a0) or not isinstance(candidate, str) or not HEX64.fullmatch(candidate):
        raise VerificationError("checkpoint snapshot hashes are invalid")
    component_hashes_by_snapshot: dict[str, dict[str, str]] = {}
    for digest in (a0, candidate):
        snapshot = _load_json(state_dir / "snapshots" / f"{digest}.json", root=state_dir)
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != "evoagent-seagym-harness-v1":
            raise VerificationError("harness snapshot schema is invalid")
        if snapshot.get("snapshot_sha256") != digest:
            raise VerificationError("harness snapshot content address is invalid")
        if snapshot.get("evaluation_only") is not True:
            raise VerificationError("harness snapshot is not evaluation-only")
        if snapshot.get("causal_attribution_claimed") is not False or snapshot.get("promotion_claimed") is not False:
            raise VerificationError("harness snapshot exceeds the claim boundary")
        components = snapshot.get("components")
        component_hashes = snapshot.get("component_sha256")
        if not isinstance(components, dict) or set(components) != {"skills", "memory", "router", "policy"}:
            raise VerificationError("harness components are invalid")
        expected_components = {name: _canonical_sha(components[name]) for name in sorted(components)}
        if component_hashes != expected_components:
            raise VerificationError("harness component hashes are invalid")
        previous_components = component_hashes_by_snapshot.get(digest)
        if previous_components is not None and previous_components != expected_components:
            raise VerificationError("duplicate snapshot digest has conflicting component hashes")
        component_hashes_by_snapshot[digest] = expected_components
        unsigned = dict(snapshot)
        unsigned.pop("snapshot_sha256")
        if _canonical_sha(unsigned) != digest:
            raise VerificationError("harness snapshot hash is invalid")
    for relative in sorted(key for key in inventory if key.startswith("attempts/")):
        attempt = _load_json(state_dir / relative, root=state_dir)
        if (
            not isinstance(attempt, dict)
            or attempt.get("schema_version") != "evoagent-seagym-update-attempt-v1"
            or attempt.get("seed") != 42
            or attempt.get("model_id") != EXPECTED_MODEL_API
        ):
            raise VerificationError("update attempt model or seed drifted")
        model_call_executed = attempt.get("model_call_executed")
        if not isinstance(model_call_executed, bool):
            raise VerificationError("update attempt lacks an explicit model-call decision")
        if model_call_executed:
            if attempt.get("status") == "skipped_no_usable_atif" or attempt.get("skip_code") is not None:
                raise VerificationError("model-backed attempt claims an ATIF skip")
        elif (
            attempt.get("status") != "skipped_no_usable_atif"
            or attempt.get("skip_code") != NO_USABLE_ATIF_SKIP_CODE
            or attempt.get("response_sha256") is not None
            or attempt.get("served_model_id") is not None
            or attempt.get("provider") is not None
            or attempt.get("usage")
            != {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}
        ):
            raise VerificationError("update attempt no-call evidence is invalid")
    return a0, candidate, component_hashes_by_snapshot


def _validate_updates(run_dir: Path) -> tuple[list[dict[str, Any]], dict[int, str]]:
    source = run_dir / "records" / "agent_updates.jsonl"
    _scan_secret_bytes(source)
    rows = _load_jsonl(source, root=run_dir, expected=2)
    safe: list[dict[str, Any]] = []
    candidate_by_update: dict[int, str] = {}
    for expected_index, row in enumerate(rows, start=1):
        _reject_nonempty_reasoning(row)
        if row.get("global_update_index") != expected_index or row.get("train_batch_index") != expected_index:
            raise VerificationError("agent update sequence drifted")
        summary = row.get("summary")
        if not isinstance(summary, dict):
            raise VerificationError("agent update summary is missing")
        if summary.get("update_index") != expected_index or summary.get("type") != "baseline_update":
            raise VerificationError("agent update identity drifted")
        logs = summary.get("logs") or {}
        if not isinstance(logs, dict):
            raise VerificationError("agent update logs are invalid")
        if logs.get("causal_attribution_claimed", False) is not False or logs.get("promotion_claimed", False) is not False:
            raise VerificationError("agent update claims causality or promotion")
        candidate = logs.get("candidate_sha256")
        if candidate is not None:
            if not isinstance(candidate, str) or not HEX64.fullmatch(candidate):
                raise VerificationError("agent update candidate hash is invalid")
            candidate_by_update[expected_index] = candidate
        metrics = summary.get("metrics") or {}
        if not isinstance(metrics, dict):
            raise VerificationError("agent update metrics are invalid")
        model_call_executed = logs.get("model_call_executed")
        if not isinstance(model_call_executed, bool):
            raise VerificationError("agent update lacks an explicit model-call decision")
        skip_code = logs.get("skip_code")
        if model_call_executed:
            if skip_code is not None:
                raise VerificationError("a model-backed update cannot claim a no-evidence skip")
        else:
            if (
                skip_code != NO_USABLE_ATIF_SKIP_CODE
                or summary.get("changed") is not False
                or summary.get("status") != "unchanged"
                or candidate is None
            ):
                raise VerificationError("agent no-call update is not the frozen ATIF skip")
            for key in ("input_tokens", "output_tokens", "cost_usd"):
                if _finite_number(metrics.get(key), f"skipped update {key}") != 0.0:
                    raise VerificationError("skipped update contains fabricated model usage")
        safe.append(
            {
                "update_index": expected_index,
                "changed": summary.get("changed") is True,
                "status": str(summary.get("status", "unknown"))[:40],
                "candidate_sha256": candidate,
                "evidence_sha256": logs.get("evidence_sha256") if isinstance(logs.get("evidence_sha256"), str) else None,
                "input_tokens": _optional_number(metrics.get("input_tokens")),
                "output_tokens": _optional_number(metrics.get("output_tokens")),
                "cost_usd": _optional_number(metrics.get("cost_usd")),
                "model_call_executed": model_call_executed,
                "skip_code": skip_code if isinstance(skip_code, str) else None,
                "causal_attribution_claimed": False,
                "promotion_claimed": False,
            }
        )
    return safe, candidate_by_update


def _optional_number(value: Any) -> float | None:
    if value is None:
        return None
    return _finite_number(value, "optional metric")


def _trial_payload(row: dict[str, Any], run_dir: Path) -> tuple[dict[str, Any], Path]:
    refs = row.get("refs")
    raw_path = refs.get("result_path") if isinstance(refs, dict) else None
    if not isinstance(raw_path, str) or not raw_path:
        raise VerificationError("task row lacks a Harbor result_path")
    jobs_root = (run_dir / "harbor" / "jobs").resolve(strict=True)
    result_path = Path(raw_path)
    if not result_path.is_absolute():
        result_path = jobs_root / result_path
    result_path = _regular_file(result_path, root=jobs_root)
    _scan_secret_bytes(result_path)
    payload = _load_json(result_path, root=jobs_root)
    if not isinstance(payload, dict):
        raise VerificationError("Harbor trial result is not an object")
    # TrialConfig legitimately persists the locked control
    # `route_contract.reasoning={"enabled": false}`.  Hidden model material can
    # only enter the normalized result through agent_result; the raw runtime is
    # deleted inside the task container and the ATIF is checked separately.
    _reject_nonempty_reasoning(payload.get("agent_result"))
    return payload, result_path


def _validate_harbor_timing(value: Any, label: str, *, required: bool) -> None:
    if value is None:
        if required:
            raise VerificationError(f"completed Harbor trial lacks {label} timing")
        return
    if not isinstance(value, dict) or set(value) != EXPECTED_TIMING_KEYS:
        raise VerificationError(f"Harbor {label} timing schema drifted")
    started_at = value.get("started_at")
    finished_at = value.get("finished_at")
    _validate_iso_timestamp(started_at, f"Harbor {label} started_at")
    if finished_at is not None:
        _validate_iso_timestamp(finished_at, f"Harbor {label} finished_at")
    if required and finished_at is None:
        raise VerificationError(f"completed Harbor trial lacks {label} finished_at")


def _validate_iso_timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise VerificationError(f"{label} is missing")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise VerificationError(f"{label} is not an ISO-8601 timestamp") from exc


def _validate_harbor_trial_result_shape(
    payload: dict[str, Any],
    result_path: Path,
    task_id: str,
    *,
    error_present: bool,
) -> tuple[str, dict[str, Any]]:
    if set(payload) != EXPECTED_TRIAL_RESULT_KEYS:
        raise VerificationError("Harbor child TrialResult schema drifted")
    try:
        trial_id = str(UUID(str(payload.get("id"))))
    except (TypeError, ValueError, AttributeError) as exc:
        raise VerificationError("Harbor child trial id is invalid") from exc
    if trial_id != payload.get("id"):
        raise VerificationError("Harbor child trial id is non-canonical")

    task_name = task_id.rsplit("/", 1)[-1]
    if payload.get("task_name") != task_name:
        raise VerificationError("Harbor child task name drifted")
    trial_name = payload.get("trial_name")
    if not isinstance(trial_name, str) or not trial_name or trial_name != result_path.parent.name:
        raise VerificationError("Harbor child trial name is invalid")
    trial_uri = payload.get("trial_uri")
    if not isinstance(trial_uri, str) or not trial_uri.startswith("file://"):
        raise VerificationError("Harbor child trial URI is invalid")
    serialized_task_id = payload.get("task_id")
    if not isinstance(serialized_task_id, dict) or set(serialized_task_id) != {"path"}:
        raise VerificationError("Harbor child LocalTaskId schema drifted")
    task_path = serialized_task_id.get("path")
    if not isinstance(task_path, str) or not task_path or Path(task_path).name != task_name:
        raise VerificationError("Harbor child LocalTaskId does not bind the frozen task")
    if not isinstance(payload.get("source"), str) or not payload["source"]:
        raise VerificationError("Harbor child task source is invalid")
    if not isinstance(payload.get("task_checksum"), str) or not HEX64.fullmatch(payload["task_checksum"]):
        raise VerificationError("Harbor child task checksum is invalid")

    config = payload.get("config")
    if not isinstance(config, dict) or set(config) != EXPECTED_TRIAL_CONFIG_KEYS:
        raise VerificationError("Harbor child TrialConfig schema drifted")
    if config.get("trial_name") != trial_name or config.get("install_only") is not False:
        raise VerificationError("Harbor child TrialConfig identity drifted")
    config_task = config.get("task")
    if not isinstance(config_task, dict) or config_task.get("path") != task_path:
        raise VerificationError("Harbor child TrialConfig task identity drifted")
    config_agent = config.get("agent")
    if (
        not isinstance(config_agent, dict)
        or config_agent.get("import_path") != "seagym_evoagent.harbor_agent:EvoAgentMiMo"
        or config_agent.get("model_name") != EXPECTED_MODEL_HARBOR
    ):
        raise VerificationError("Harbor child TrialConfig agent identity drifted")
    if payload.get("agent_info") != EXPECTED_HARBOR_AGENT_INFO:
        raise VerificationError("Harbor child AgentInfo drifted")

    agent_result = payload.get("agent_result")
    if not isinstance(agent_result, dict) or set(agent_result) != EXPECTED_AGENT_CONTEXT_KEYS:
        raise VerificationError("Harbor child AgentContext schema drifted")
    if agent_result.get("rollout_details") is not None or not isinstance(agent_result.get("metadata"), dict):
        raise VerificationError("Harbor child AgentContext contains unapproved rollout detail")
    for key in ("n_input_tokens", "n_cache_tokens", "n_output_tokens"):
        _bounded_int(agent_result.get(key), f"Harbor child {key}")
    _finite_number(agent_result.get("cost_usd"), "Harbor child cost_usd")

    verifier_result = payload.get("verifier_result")
    if not isinstance(verifier_result, dict) or set(verifier_result) != {"rewards"}:
        raise VerificationError("Harbor child VerifierResult schema drifted")
    if not isinstance(verifier_result.get("rewards"), dict):
        raise VerificationError("Harbor child rewards are invalid")
    exception_info = payload.get("exception_info")
    if error_present:
        expected_exception_keys = {
            "exception_type",
            "exception_message",
            "exception_traceback",
            "occurred_at",
        }
        if not isinstance(exception_info, dict) or set(exception_info) != expected_exception_keys:
            raise VerificationError("Harbor child ExceptionInfo schema drifted")
        for key in ("exception_type", "exception_message", "exception_traceback", "occurred_at"):
            if not isinstance(exception_info.get(key), str):
                raise VerificationError("Harbor child ExceptionInfo field is invalid")
        if not exception_info["exception_type"] or not exception_info["occurred_at"]:
            raise VerificationError("Harbor child ExceptionInfo identity is missing")
        _validate_iso_timestamp(exception_info["occurred_at"], "Harbor child exception occurred_at")
    elif exception_info is not None:
        raise VerificationError("non-errored Harbor child contains ExceptionInfo")

    for key in ("started_at", "finished_at"):
        _validate_iso_timestamp(payload.get(key), f"Harbor child {key}")
    for timing_key in ("environment_setup", "agent_setup", "agent_execution", "verifier"):
        _validate_harbor_timing(
            payload.get(timing_key),
            timing_key,
            required=not error_present,
        )
    if payload.get("step_results") is not None:
        raise VerificationError("single-step Terminal-Bench trial contains step_results")
    return trial_id, agent_result


def _validate_harbor_job_config_payload(
    config: Any,
    job_dir: Path,
    jobs_root: Path,
    task_id: str,
    *,
    require_retry: bool,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise VerificationError("Harbor single-task job config is not an object")
    if config.get("job_name") != job_dir.name:
        raise VerificationError("Harbor job config name differs from its directory")
    raw_jobs_dir = config.get("jobs_dir")
    if not isinstance(raw_jobs_dir, str) or not raw_jobs_dir or not Path(raw_jobs_dir).is_absolute():
        raise VerificationError("Harbor job config jobs_dir is invalid")
    if Path(raw_jobs_dir).resolve() != jobs_root:
        raise VerificationError("Harbor job config jobs_dir drifted")
    if config.get("n_attempts") != 1 or config.get("n_concurrent_trials") != 1:
        raise VerificationError("Harbor job config is not one-task/one-attempt serial execution")
    retry = config.get("retry")
    if (require_retry and not isinstance(retry, dict)) or (
        retry is not None and (not isinstance(retry, dict) or retry.get("max_retries") != 0)
    ):
        raise VerificationError("Harbor job config enables retries")
    agents = config.get("agents")
    if not isinstance(agents, list) or len(agents) != 1 or not isinstance(agents[0], dict):
        raise VerificationError("Harbor job config agent cardinality drifted")

    task_name = task_id.rsplit("/", 1)[-1]
    tasks = config.get("tasks")
    datasets = config.get("datasets")
    if not isinstance(tasks, list) or not isinstance(datasets, list):
        raise VerificationError("Harbor job config task selection is invalid")
    if tasks != [] or len(datasets) != 1 or not isinstance(datasets[0], dict):
        raise VerificationError("Harbor job config must use exactly one patched dataset")
    dataset = datasets[0]
    patched_job_dir = jobs_root / "_patched_tasksets" / job_dir.name
    raw_dataset_path = dataset.get("path")
    if (
        not isinstance(raw_dataset_path, str)
        or not Path(raw_dataset_path).is_absolute()
        or Path(raw_dataset_path).resolve() != patched_job_dir.resolve(strict=True)
        or dataset.get("task_names") != [task_name]
        or dataset.get("n_tasks") != 1
    ):
        raise VerificationError("Harbor job config patched dataset identity drifted")
    return config


def _validate_harbor_job_config(job_dir: Path, jobs_root: Path, task_id: str) -> dict[str, Any]:
    config_path = job_dir / "config.json"
    _scan_secret_bytes(config_path)
    return _validate_harbor_job_config_payload(
        _load_json(config_path, root=jobs_root),
        job_dir,
        jobs_root,
        task_id,
        require_retry=True,
    )


def _validate_atif(
    path: Path,
    attestation: dict[str, Any],
    *,
    expected_snapshot: str,
    expected_component_hashes: dict[str, str],
    root: Path,
) -> int:
    _scan_secret_bytes(path)
    atif = _load_json(path, root=root, max_bytes=8 * 1024 * 1024)
    _reject_nonempty_reasoning(atif)
    if (
        not isinstance(atif, dict)
        or set(atif) != {"schema_version", "agent", "steps", "final_metrics", "extra"}
        or atif.get("schema_version") != "ATIF-v1.7"
    ):
        raise VerificationError("sanitized ATIF schema is invalid")
    forbidden = {"session_id", "trajectory_id", "notes", "subagent_trajectories"}
    if forbidden & set(atif):
        raise VerificationError("sanitized ATIF contains forbidden identity/transcript fields")
    extra = atif.get("extra")
    expected_extra = {
        "api_model_id": EXPECTED_MODEL_API,
        "seed": 42,
        "snapshot_hash": expected_snapshot,
        "component_hashes": expected_component_hashes,
        "runtime_identity": {"name": "mimocode", "version": EXPECTED_MIMOCODE["version"]},
        "route_contract_sha256": EXPECTED_ROUTE_CONTRACT_SHA256,
    }
    if extra != expected_extra:
        raise VerificationError("ATIF snapshot, component, runtime, or route identity drifted")
    agent = atif.get("agent")
    expected_agent = {
        "name": "seagym-evoagent-mimocode",
        "version": EXPECTED_ADAPTER_VERSION,
        "model_name": EXPECTED_MODEL_HARBOR,
        "extra": expected_extra,
    }
    if agent != expected_agent:
        raise VerificationError("sanitized ATIF agent identity drifted")
    steps = atif.get("steps")
    if not isinstance(steps, list) or not steps:
        raise VerificationError("sanitized ATIF has no steps")
    allowed_status = {
        "status:pending",
        "status:running",
        "status:success",
        "status:error",
        "status:timeout",
        "status:cancelled",
        "status:unknown",
    }
    aggregate: dict[str, int | float] = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cached_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.0,
    }
    seen_metrics: set[str] = set()
    saw_reasoning_telemetry = False
    llm_call_count = 0
    for expected, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or step.get("step_id") != expected or step.get("message") != "":
            raise VerificationError("sanitized ATIF step is invalid")
        if expected == 1:
            if step != {"step_id": 1, "source": "system", "message": "", "extra": {"status": "sanitized"}}:
                raise VerificationError("sanitized ATIF system boundary step is invalid")
            continue
        allowed_step_keys = {
            "step_id",
            "source",
            "message",
            "model_name",
            "timestamp",
            "metrics",
            "llm_call_count",
            "tool_calls",
            "observation",
            "extra",
        }
        if set(step) - allowed_step_keys or step.get("source") != "agent" or step.get("model_name") != EXPECTED_MODEL_HARBOR:
            raise VerificationError("sanitized ATIF agent step identity is invalid")
        if "timestamp" in step and (
            not isinstance(step["timestamp"], str)
            or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", step["timestamp"])
        ):
            raise VerificationError("sanitized ATIF timestamp is invalid")
        metrics = step.get("metrics")
        if metrics is not None:
            allowed_metric_keys = {"prompt_tokens", "completion_tokens", "cached_tokens", "cost_usd", "extra"}
            if not isinstance(metrics, dict) or not metrics or set(metrics) - allowed_metric_keys:
                raise VerificationError("sanitized ATIF metrics shape is invalid")
            for key, value in metrics.items():
                if key == "extra":
                    if not isinstance(value, dict) or set(value) != {"reasoning_tokens"}:
                        raise VerificationError("sanitized ATIF reasoning telemetry is invalid")
                    aggregate["reasoning_tokens"] += _bounded_int(
                        value["reasoning_tokens"],
                        "ATIF step reasoning tokens",
                    )
                    saw_reasoning_telemetry = True
                    continue
                if key == "cost_usd":
                    parsed: int | float = _finite_number(value, "ATIF step cost")
                else:
                    parsed = _bounded_int(value, f"ATIF step {key}")
                aggregate[key] += parsed
                seen_metrics.add(key)
            if metrics.get("cached_tokens", 0) > metrics.get("prompt_tokens", 0):
                raise VerificationError("ATIF step cached tokens exceed prompt tokens")
            if step.get("llm_call_count") != 1:
                raise VerificationError("sanitized ATIF metric step must represent one model call")
            llm_call_count += 1
        elif "llm_call_count" in step:
            raise VerificationError("sanitized ATIF llm_call_count requires metrics")
        calls = step.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or len(calls) != 1 or not isinstance(calls[0], dict):
                raise VerificationError("sanitized ATIF tool call shape is invalid")
            call = calls[0]
            if (
                set(call) != {"tool_call_id", "function_name", "arguments"}
                or not isinstance(call.get("tool_call_id"), str)
                or not re.fullmatch(r"tool-\d{6}", call["tool_call_id"])
                or call.get("function_name") not in SAFE_TOOL_NAMES
                or call.get("arguments") != {}
            ):
                raise VerificationError("sanitized ATIF retained or invented tool material")
            observation = step.get("observation")
            results = observation.get("results") if isinstance(observation, dict) and set(observation) == {"results"} else None
            if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
                raise VerificationError("sanitized ATIF observation is invalid")
            result = results[0]
            if (
                set(result) != {"source_call_id", "content"}
                or result.get("source_call_id") != call["tool_call_id"]
                or result.get("content") not in allowed_status
            ):
                raise VerificationError("sanitized ATIF retained tool output or mismatched its call")
            status = result["content"].split(":", 1)[1]
            if step.get("extra") != {"status": status}:
                raise VerificationError("sanitized ATIF tool status is inconsistent")
        elif any(key in step for key in ("observation", "extra")):
            raise VerificationError("sanitized ATIF observation metadata requires a tool call")
        if metrics is None and calls is None:
            raise VerificationError("sanitized ATIF agent step has no structural evidence")
    final_metrics = atif.get("final_metrics")
    expected_metric_keys = {"total_steps"} | {
        ("total_cost_usd" if name == "cost_usd" else f"total_{name}")
        for name in seen_metrics
    }
    if saw_reasoning_telemetry:
        expected_metric_keys.add("extra")
    if not isinstance(final_metrics, dict) or set(final_metrics) != expected_metric_keys or final_metrics.get("total_steps") != len(steps):
        raise VerificationError("sanitized ATIF final metrics shape is invalid")
    for name in seen_metrics:
        total_name = "total_cost_usd" if name == "cost_usd" else f"total_{name}"
        actual = final_metrics.get(total_name)
        expected_total = aggregate[name]
        if name == "cost_usd":
            if not math.isclose(_finite_number(actual, "ATIF total cost"), float(expected_total), abs_tol=1e-12):
                raise VerificationError("sanitized ATIF final cost does not match its steps")
        elif _bounded_int(actual, f"ATIF {total_name}") != expected_total:
            raise VerificationError("sanitized ATIF final tokens do not match its steps")
    if saw_reasoning_telemetry:
        final_extra = final_metrics.get("extra")
        if not isinstance(final_extra, dict) or set(final_extra) != {"total_reasoning_tokens"}:
            raise VerificationError("sanitized ATIF final reasoning telemetry is invalid")
        if _bounded_int(final_extra["total_reasoning_tokens"], "ATIF total reasoning tokens") != aggregate["reasoning_tokens"]:
            raise VerificationError("sanitized ATIF final reasoning tokens do not match its steps")
    atif_usage = {
        "prompt_tokens": int(aggregate["prompt_tokens"]),
        "completion_tokens": int(aggregate["completion_tokens"]),
        "cached_tokens": int(aggregate["cached_tokens"]),
        "reasoning_tokens": int(aggregate["reasoning_tokens"]),
        "cost_usd": float(aggregate["cost_usd"]),
    }
    attested_usage = attestation.get("usage")
    if not isinstance(attested_usage, dict) or set(attested_usage) != set(atif_usage):
        raise VerificationError("Harbor attestation usage shape is invalid")
    for key, expected_value in atif_usage.items():
        actual = attested_usage.get(key)
        if key == "cost_usd":
            if not math.isclose(_finite_number(actual, "attestation cost_usd"), expected_value, abs_tol=1e-12):
                raise VerificationError("Harbor attestation cost differs from ATIF")
        elif _bounded_int(actual, f"attestation {key}") != expected_value:
            raise VerificationError("Harbor attestation tokens differ from ATIF")
    if _sha256(path, max_bytes=8 * 1024 * 1024) != attestation.get("atif_sha256"):
        raise VerificationError("ATIF hash does not match its attestation")
    return llm_call_count


def _validate_attestation(
    result_path: Path,
    expected_snapshot: str,
    expected_component_hashes: dict[str, str],
) -> tuple[dict[str, Any], str, int]:
    trial_dir = result_path.parent
    lexical_agent_dir = trial_dir / "agent"
    if _is_linklike(lexical_agent_dir):
        raise VerificationError("Harbor agent evidence directory is invalid")
    try:
        agent_dir = lexical_agent_dir.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("Harbor agent evidence directory is missing") from exc
    if _is_linklike(agent_dir) or agent_dir.parent != trial_dir.resolve(strict=True):
        raise VerificationError("Harbor agent evidence directory is invalid")
    attestation_path = _regular_file(agent_dir / "evoagent-attestation.json", root=trial_dir)
    _scan_secret_bytes(attestation_path)
    attestation = _load_json(attestation_path, root=trial_dir)
    expected_attestation_keys = {
        "schema_version",
        "snapshot_sha256",
        "component_sha256",
        "atif_sha256",
        "route_contract_sha256",
        "model",
        "seed",
        "runtime",
        "usage",
        "runtime_failure_receipt_sha256",
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_persisted",
        "causal_attribution_claimed",
        "promotion_claimed",
        "activation_claimed",
        "attestation_sha256",
    }
    if (
        not isinstance(attestation, dict)
        or set(attestation) != expected_attestation_keys
        or attestation.get("schema_version") != "evoagent-harbor-attestation-v1"
    ):
        raise VerificationError("EvoAgent Harbor attestation schema is invalid")
    unsigned = dict(attestation)
    claimed_hash = unsigned.pop("attestation_sha256", None)
    if not isinstance(claimed_hash, str) or not HEX64.fullmatch(claimed_hash) or _canonical_sha(unsigned) != claimed_hash:
        raise VerificationError("EvoAgent Harbor attestation hash is invalid")
    if attestation.get("snapshot_sha256") != expected_snapshot:
        raise VerificationError("Harbor trial used the wrong EvoAgent snapshot")
    if attestation.get("component_sha256") != expected_component_hashes:
        raise VerificationError("Harbor trial component hashes differ from the verified snapshot")
    if attestation.get("route_contract_sha256") != EXPECTED_ROUTE_CONTRACT_SHA256:
        raise VerificationError("Harbor trial route-contract hash drifted")
    if attestation.get("seed") != 42:
        raise VerificationError("Harbor trial seed drifted")
    model = attestation.get("model")
    expected_model = {
        "api_id": EXPECTED_MODEL_API,
        "harbor_id": EXPECTED_MODEL_HARBOR,
        "openrouter_provider": EXPECTED_ENDPOINT,
        "fallbacks_allowed": False,
        "reasoning_enabled": False,
        "credential_transport": "local_guard_proxy_v1",
    }
    if model != expected_model:
        raise VerificationError("Harbor trial model route attestation drifted")
    runtime = attestation.get("runtime")
    expected_runtime = {
        "adapter_version": EXPECTED_ADAPTER_VERSION,
        "mimocode_version": EXPECTED_MIMOCODE["version"],
        "mimocode_archive_sha256": EXPECTED_MIMOCODE["asset_sha256"],
        "seagym_commit": EXPECTED_UPSTREAM["seagym"],
        "harbor_commit": EXPECTED_UPSTREAM["harbor_seagym_gitlink"],
    }
    if runtime != expected_runtime:
        raise VerificationError("Harbor trial runtime identity drifted")
    failure_receipt_sha256 = attestation.get("runtime_failure_receipt_sha256")
    if failure_receipt_sha256 is not None and (
        not isinstance(failure_receipt_sha256, str) or not HEX64.fullmatch(failure_receipt_sha256)
    ):
        raise VerificationError("Harbor attestation runtime-failure receipt hash is invalid")
    for key in ("raw_prompt_persisted", "raw_response_persisted", "reasoning_persisted", "causal_attribution_claimed", "promotion_claimed", "activation_claimed"):
        if attestation.get(key) is not False:
            raise VerificationError(f"Harbor attestation violates boundary: {key}")
    llm_call_count = _validate_atif(
        agent_dir / "trajectory.json",
        attestation,
        expected_snapshot=expected_snapshot,
        expected_component_hashes=expected_component_hashes,
        root=trial_dir,
    )
    return attestation, claimed_hash, llm_call_count


def _validate_failure_receipt(
    row: dict[str, Any],
    result_path: Path,
    expected_snapshot: str,
    expected_component_hashes: dict[str, str],
    expected_atif_present: bool,
) -> tuple[dict[str, Any], str]:
    trial_dir = result_path.parent.resolve(strict=True)
    lexical_agent_dir = trial_dir / "agent"
    if _is_linklike(lexical_agent_dir):
        raise VerificationError("Harbor failure evidence directory is invalid")
    try:
        agent_dir = lexical_agent_dir.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("Harbor failure evidence directory is missing") from exc
    if _is_linklike(agent_dir) or agent_dir.parent != trial_dir:
        raise VerificationError("Harbor failure evidence directory is invalid")
    receipt_path = _regular_file(agent_dir / FAILURE_RECEIPT_FILENAME, root=trial_dir, max_bytes=64 * 1024)
    refs = row.get("refs") or {}
    explicit = refs.get("failure_receipt_path") if isinstance(refs, dict) else None
    if explicit is not None:
        if not isinstance(explicit, str) or not explicit:
            raise VerificationError("task failure receipt reference is invalid")
        jobs_root = result_path.parents[2].resolve(strict=True)
        explicit_path = Path(explicit)
        if not explicit_path.is_absolute():
            explicit_path = jobs_root / explicit_path
        if _regular_file(explicit_path, root=jobs_root, max_bytes=64 * 1024) != receipt_path:
            raise VerificationError("task failure receipt reference does not match its Harbor trial")
    _scan_secret_bytes(receipt_path)
    receipt = _load_json(receipt_path, root=trial_dir, max_bytes=64 * 1024)
    expected_keys = {
        "schema_version",
        "failure_class",
        "failure_stage",
        "mimocode_exit_class",
        "snapshot_sha256",
        "component_sha256",
        "route_contract_sha256",
        "model",
        "seed",
        "runtime",
        "atif_present",
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_content_persisted",
        "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise VerificationError("runtime failure receipt schema is invalid")
    _reject_nonempty_reasoning(receipt)
    if receipt.get("schema_version") != FAILURE_RECEIPT_SCHEMA:
        raise VerificationError("runtime failure receipt version drifted")
    unsigned = dict(receipt)
    claimed_hash = unsigned.pop("receipt_sha256", None)
    if not isinstance(claimed_hash, str) or not HEX64.fullmatch(claimed_hash) or _canonical_sha(unsigned) != claimed_hash:
        raise VerificationError("runtime failure receipt hash is invalid")
    failure_class = receipt.get("failure_class")
    failure_stage = receipt.get("failure_stage")
    exit_class = receipt.get("mimocode_exit_class")
    if failure_class not in FAILURE_RECEIPT_CLASSES:
        raise VerificationError("runtime failure receipt class is invalid")
    if failure_stage not in FAILURE_RECEIPT_STAGES or exit_class not in MIMOCODE_EXIT_CLASSES:
        raise VerificationError("runtime failure receipt classification is invalid")
    expected_pair = {
        "mimocode_process_failed": ("mimocode", False),
        "runtime_sanitization_failed": ("sanitize", True),
        "mimocode_and_sanitization_failed": ("sanitize", False),
    }[failure_class]
    if failure_stage != expected_pair[0] or (exit_class == "success") is not expected_pair[1]:
        raise VerificationError("runtime failure receipt classification is inconsistent")
    if receipt.get("snapshot_sha256") != expected_snapshot:
        raise VerificationError("runtime failure receipt snapshot drifted")
    if receipt.get("component_sha256") != expected_component_hashes:
        raise VerificationError("runtime failure receipt component hashes drifted")
    if receipt.get("route_contract_sha256") != EXPECTED_ROUTE_CONTRACT_SHA256:
        raise VerificationError("runtime failure receipt route contract drifted")
    if receipt.get("model") != {"api_id": EXPECTED_MODEL_API, "harbor_id": EXPECTED_MODEL_HARBOR}:
        raise VerificationError("runtime failure receipt model route drifted")
    if receipt.get("seed") != 42:
        raise VerificationError("runtime failure receipt seed drifted")
    if receipt.get("runtime") != {"name": "mimocode", "version": EXPECTED_MIMOCODE["version"]}:
        raise VerificationError("runtime failure receipt runtime identity drifted")
    if receipt.get("atif_present") is not expected_atif_present:
        raise VerificationError("runtime failure receipt ATIF state drifted")
    for key in (
        "raw_prompt_persisted",
        "raw_response_persisted",
        "reasoning_content_persisted",
    ):
        if receipt.get(key) is not False:
            raise VerificationError(f"runtime failure receipt violates boundary: {key}")
    if not expected_atif_present:
        for unexpected in ("trajectory.json", "atif.json", "evoagent-attestation.json"):
            path = agent_dir / unexpected
            if path.exists() or _is_linklike(path):
                raise VerificationError("runtime failure receipt conflicts with partial ATIF evidence")
    return receipt, claimed_hash


def _failure_row_usage(payload: dict[str, Any], row: dict[str, Any]) -> dict[str, int | float]:
    agent_result = payload.get("agent_result")
    if agent_result is None:
        agent_result = {}
    if not isinstance(agent_result, dict):
        raise VerificationError("failed Harbor trial agent_result is invalid")
    cost = row.get("cost") or {}
    if not isinstance(cost, dict):
        raise VerificationError("failed normalized task cost is invalid")
    fields = {
        "prompt_tokens": ("n_input_tokens", "n_input_tokens"),
        "cached_tokens": ("n_cache_tokens", "n_cache_tokens"),
        "completion_tokens": ("n_output_tokens", "n_output_tokens"),
        "cost_usd": ("cost_usd", "cost_usd"),
    }
    usage: dict[str, int | float] = {}
    for output_name, (payload_name, row_name) in fields.items():
        payload_value = agent_result.get(payload_name, 0)
        row_value = cost.get(row_name, 0)
        if output_name == "cost_usd":
            parsed_payload: int | float = _finite_number(payload_value, "failed Harbor cost")
            parsed_row = _finite_number(row_value, "failed normalized cost")
        else:
            parsed_payload = _bounded_int(payload_value, f"failed Harbor {output_name}")
            parsed_row = _bounded_int(row_value, f"failed normalized {output_name}")
        if parsed_payload != parsed_row:
            raise VerificationError("failed Harbor usage differs from the normalized row")
        usage[output_name] = parsed_row
    usage["reasoning_tokens"] = 0
    return usage


def _row_expected_snapshot(row: dict[str, Any], snapshots: dict[str, str]) -> str:
    mode = row.get("mode")
    batch = row.get("train_batch_index")
    role = row.get("baseline_role")
    point = row.get("evaluation_point_id")
    if role == "A_0":
        return snapshots["A0"]
    if role == "A_T":
        return snapshots["AT"]
    if mode == "validation" and point == "E_0":
        return snapshots["A0"]
    if mode == "train" and batch == 1:
        return snapshots["A0"]
    if mode == "replay" and batch == 1:
        return snapshots["E1"]
    if mode == "train" and batch == 2:
        return snapshots["E1"]
    if mode in {"replay", "validation"} and batch == 2:
        return snapshots["AT"]
    raise VerificationError("task row does not map to a frozen snapshot phase")


def _validate_rows(
    run_dir: Path,
    split: dict[str, Any],
    plan: dict[str, Any],
    task_index: dict[str, Any],
    snapshots: dict[str, str],
    component_hashes_by_snapshot: dict[str, dict[str, str]],
) -> tuple[list[dict[str, Any]], dict[str, Any], int]:
    source = run_dir / "records" / "task_results.jsonl"
    _scan_secret_bytes(source)
    rows = _load_jsonl(source, root=run_dir, expected=24)
    known_tasks = {item["task_id"]: item for item in task_index["tasks"]}
    splits = split["splits"]
    replay = plan["views"]["replay"]
    expected_counts = Counter({"validation": 6, "train": 6, "replay": 6, "final": 3, "final_baseline": 3})
    actual_counts: Counter[str] = Counter()
    phase_tasks: dict[str, list[str]] = defaultdict(list)
    actual_slot_order: list[tuple[Any, ...]] = []
    safe_rows: list[dict[str, Any]] = []
    attestation_hashes: list[str] = []
    failure_receipt_hashes: list[str] = []
    failure_receipt_paths: set[Path] = set()
    harbor_job_dirs: set[Path] = set()
    harbor_job_ids: set[str] = set()
    harbor_trial_ids: set[str] = set()
    harbor_result_paths: set[Path] = set()
    harbor_task_by_job_dir: dict[Path, str] = {}
    missing_atif_receipt_count = 0
    rollout_llm_call_count = 0
    for row in rows:
        _reject_nonempty_reasoning(row)
        task_id = row.get("task_id")
        if task_id not in known_tasks:
            raise VerificationError("task result contains an unknown task")
        if row.get("run_id") != plan.get("run_id") or row.get("experiment_id") != plan.get("experiment_id") or row.get("split_id") != split.get("split_id"):
            raise VerificationError("task result run identity drifted")
        score = _finite_number(row.get("score"), "task score", maximum=1.0)
        if score not in (0.0, 1.0):
            raise VerificationError("Terminal-Bench pilot score must be binary")
        success = row.get("success") is True
        error_present = row.get("error") not in (None, "")
        effective_score = 0.0 if error_present else score
        if success != (effective_score >= 1.0):
            raise VerificationError("task success and effective score disagree")
        role = row.get("baseline_role")
        mode = row.get("mode")
        if role == "A_0":
            phase = "final_baseline"
        elif role == "A_T":
            phase = "final"
        elif mode in {"validation", "train", "replay"}:
            phase = str(mode)
        else:
            raise VerificationError("task result phase is invalid")
        actual_counts[phase] += 1
        phase_tasks[phase].append(task_id)
        actual_slot_order.append(
            (
                mode,
                row.get("view_name"),
                role,
                row.get("train_batch_index"),
                row.get("evaluation_point_id"),
                row.get("agent_checkpoint_id"),
                task_id,
            )
        )
        payload, result_path = _trial_payload(row, run_dir)
        refs = row.get("refs") or {}
        jobs_root = (run_dir / "harbor" / "jobs").resolve(strict=True)
        if result_path in harbor_result_paths:
            raise VerificationError("a Harbor child result was reused across planned trials")
        harbor_result_paths.add(result_path)
        trial_id, agent_context = _validate_harbor_trial_result_shape(
            payload,
            result_path,
            task_id,
            error_present=error_present,
        )
        if trial_id in harbor_trial_ids:
            raise VerificationError("Harbor child trial id was reused")
        harbor_trial_ids.add(trial_id)
        job_dir = result_path.parent.parent.resolve(strict=True)
        if job_dir in harbor_job_dirs:
            raise VerificationError("a Harbor job was reused across planned trials")
        harbor_job_dirs.add(job_dir)
        harbor_task_by_job_dir[job_dir] = task_id
        expected_patched_task = (
            jobs_root
            / "_patched_tasksets"
            / job_dir.name
            / task_id.rsplit("/", 1)[-1]
        )
        serialized_task_path = Path(payload["task_id"]["path"])
        try:
            resolved_serialized_task = serialized_task_path.resolve(strict=True)
            resolved_patched_task = expected_patched_task.resolve(strict=True)
        except OSError as exc:
            raise VerificationError("Harbor patched task path is missing") from exc
        if (
            payload.get("source") != job_dir.name
            or not serialized_task_path.is_absolute()
            or resolved_serialized_task != resolved_patched_task
        ):
            raise VerificationError("Harbor child task path/source is not bound to its patched dataset")
        raw_job_dir = refs.get("job_dir") if isinstance(refs, dict) else None
        if not isinstance(raw_job_dir, str) or not raw_job_dir:
            raise VerificationError("task row lacks a Harbor job_dir")
        referenced_job_dir = Path(raw_job_dir)
        if not referenced_job_dir.is_absolute():
            referenced_job_dir = jobs_root / referenced_job_dir
        try:
            referenced_job_dir = referenced_job_dir.resolve(strict=True)
        except OSError as exc:
            raise VerificationError("referenced Harbor job directory is missing") from exc
        if referenced_job_dir != job_dir or _is_linklike(referenced_job_dir) or not referenced_job_dir.is_dir():
            raise VerificationError("Harbor job_dir does not bind the child result")
        if refs.get("harbor_returncode") != 0:
            raise VerificationError("Harbor single-task job did not exit successfully")
        job_config = _validate_harbor_job_config(job_dir, jobs_root, task_id)
        if job_config["agents"][0] != payload["config"]["agent"]:
            raise VerificationError("Harbor job AgentConfig differs from its child TrialConfig")
        trial_config_path = result_path.parent / "config.json"
        _scan_secret_bytes(trial_config_path)
        if _load_json(trial_config_path, root=job_dir) != payload.get("config"):
            raise VerificationError("Harbor child config.json differs from embedded TrialConfig")
        direct_job_entries = list(job_dir.iterdir())
        if any(_is_linklike(path) for path in direct_job_entries):
            raise VerificationError("Harbor job contains a link-like entry")
        child_directories = {
            path.resolve(strict=True)
            for path in direct_job_entries
            if path.is_dir()
        }
        if child_directories != {result_path.parent.resolve(strict=True)}:
            raise VerificationError(
                "Harbor job does not contain exactly one child result/trial directory"
            )
        aggregate_path = job_dir / "result.json"
        _scan_secret_bytes(aggregate_path)
        aggregate = _load_json(aggregate_path, root=jobs_root)
        expected_aggregate_keys = {
            "finished_at",
            "id",
            "n_total_trials",
            "started_at",
            "stats",
            "updated_at",
        }
        if not isinstance(aggregate, dict) or set(aggregate) != expected_aggregate_keys:
            raise VerificationError("Harbor single-task job aggregate schema drifted")
        try:
            job_id = str(UUID(str(aggregate.get("id"))))
        except (TypeError, ValueError, AttributeError) as exc:
            raise VerificationError("Harbor single-task job id is invalid") from exc
        if job_id != aggregate.get("id") or job_id in harbor_job_ids:
            raise VerificationError("Harbor single-task job id was reused or is non-canonical")
        harbor_job_ids.add(job_id)
        if _bounded_int(
            aggregate.get("n_total_trials"),
            "Harbor total trials",
            maximum=1,
        ) != 1:
            raise VerificationError("Harbor job is not a one-task job")
        for timestamp_key in ("started_at", "updated_at", "finished_at"):
            _validate_iso_timestamp(
                aggregate.get(timestamp_key),
                f"Harbor completed-job {timestamp_key}",
            )
        stats = aggregate.get("stats") if isinstance(aggregate, dict) else None
        expected_stats_keys = {
            "cost_usd",
            "evals",
            "n_cache_tokens",
            "n_cancelled_trials",
            "n_completed_trials",
            "n_errored_trials",
            "n_input_tokens",
            "n_output_tokens",
            "n_pending_trials",
            "n_retries",
            "n_running_trials",
        }
        if not isinstance(stats, dict) or set(stats) != expected_stats_keys:
            raise VerificationError("Harbor single-task job aggregate stats schema drifted")
        if not isinstance(stats.get("evals"), dict):
            raise VerificationError("Harbor single-task job aggregate evals are invalid")
        count_keys = (
            "n_cancelled_trials",
            "n_completed_trials",
            "n_errored_trials",
            "n_pending_trials",
            "n_retries",
            "n_running_trials",
        )
        counts = {
            key: _bounded_int(stats.get(key), f"Harbor aggregate {key}", maximum=1)
            for key in count_keys
        }
        expected_zero_stats = (
            "n_running_trials",
            "n_pending_trials",
            "n_cancelled_trials",
            "n_retries",
        )
        if (
            counts["n_completed_trials"] != 1
            or counts["n_errored_trials"] not in {0, 1}
            or any(counts[key] != 0 for key in expected_zero_stats)
        ):
            raise VerificationError("Harbor single-task job aggregate is incomplete")
        child_results = sorted(job_dir.glob("*/result.json"))
        if len(child_results) != 1:
            raise VerificationError("Harbor job does not contain exactly one child result")
        only_child = _regular_file(child_results[0], root=job_dir)
        if only_child != result_path:
            raise VerificationError("Harbor aggregate child differs from the normalized result")
        child_config = payload.get("config")
        if not isinstance(child_config, dict) or child_config.get("job_id") != job_id:
            raise VerificationError("Harbor child result is not bound to its aggregate job")
        expected_errored = int(payload.get("exception_info") is not None)
        if counts["n_errored_trials"] != expected_errored:
            raise VerificationError("Harbor aggregate error count differs from its child")
        for aggregate_key, child_key in (
            ("n_input_tokens", "n_input_tokens"),
            ("n_cache_tokens", "n_cache_tokens"),
            ("n_output_tokens", "n_output_tokens"),
        ):
            if _bounded_int(
                stats.get(aggregate_key),
                f"Harbor aggregate {aggregate_key}",
            ) != _bounded_int(
                agent_context.get(child_key),
                f"Harbor child {child_key}",
            ):
                raise VerificationError("Harbor aggregate token usage differs from its child")
        if not math.isclose(
            _finite_number(stats.get("cost_usd"), "Harbor aggregate cost_usd"),
            _finite_number(agent_context.get("cost_usd"), "Harbor child cost_usd"),
            abs_tol=1e-9,
        ):
            raise VerificationError("Harbor aggregate cost differs from its child")

        agent_info = payload["agent_info"]
        model_info = agent_info["model_info"]
        expected_eval_key = f"{agent_info['name']}__{model_info['name']}__{payload['source']}"
        evals = stats["evals"]
        if set(evals) != {expected_eval_key}:
            raise VerificationError("Harbor aggregate eval identity differs from its child")
        eval_stats = evals[expected_eval_key]
        expected_eval_stats_keys = {
            "exception_stats",
            "metrics",
            "n_errors",
            "n_trials",
            "pass_at_k",
            "reward_stats",
        }
        if not isinstance(eval_stats, dict) or set(eval_stats) != expected_eval_stats_keys:
            raise VerificationError("Harbor aggregate eval stats schema drifted")
        if (
            _bounded_int(eval_stats.get("n_trials"), "Harbor eval n_trials", maximum=1) != 1
            or _bounded_int(eval_stats.get("n_errors"), "Harbor eval n_errors", maximum=1)
            != expected_errored
            or not isinstance(eval_stats.get("metrics"), list)
            or not isinstance(eval_stats.get("pass_at_k"), dict)
        ):
            raise VerificationError("Harbor aggregate eval counts are inconsistent")
        rewards_payload = payload["verifier_result"]["rewards"]
        if set(rewards_payload) != {"reward"}:
            raise VerificationError("Harbor child reward inventory drifted")
        expected_metric = {
            "mean": _finite_number(rewards_payload["reward"], "Harbor child reward")
        }
        if eval_stats["metrics"] != [expected_metric]:
            raise VerificationError("Harbor aggregate metrics differ from its child")
        reward_stats = eval_stats.get("reward_stats")
        if not isinstance(reward_stats, dict) or set(reward_stats) != set(rewards_payload):
            raise VerificationError("Harbor aggregate reward inventory differs from its child")
        for reward_name, reward_value in rewards_payload.items():
            per_value = reward_stats.get(reward_name)
            if not isinstance(per_value, dict) or len(per_value) != 1:
                raise VerificationError("Harbor aggregate reward stats are invalid")
            serialized_value, trial_names = next(iter(per_value.items()))
            try:
                parsed_value = float(serialized_value)
            except (TypeError, ValueError) as exc:
                raise VerificationError("Harbor aggregate reward value is invalid") from exc
            if (
                not math.isclose(parsed_value, _finite_number(reward_value, "Harbor child reward"), abs_tol=1e-9)
                or trial_names != [payload["trial_name"]]
            ):
                raise VerificationError("Harbor aggregate reward stats differ from its child")
        exception_stats = eval_stats.get("exception_stats")
        expected_exception_stats = (
            {payload["exception_info"]["exception_type"]: [payload["trial_name"]]}
            if expected_errored
            else {}
        )
        if exception_stats != expected_exception_stats:
            raise VerificationError("Harbor aggregate exception stats differ from its child")
        harbor_name = payload.get("task_name")
        if harbor_name not in {task_id, task_id.rsplit("/", 1)[-1]}:
            raise VerificationError("Harbor trial task identity drifted")
        if refs.get("task_checksum") != payload.get("task_checksum"):
            raise VerificationError("Harbor task checksum does not match the normalized row")
        rewards = ((payload.get("verifier_result") or {}).get("rewards") or {})
        reward = rewards.get("reward", 0.0) if isinstance(rewards, dict) else 0.0
        normalized_reward = _finite_number(reward, "Harbor reward", maximum=1.0)
        row_reward = ((row.get("rewards") or {}).get("reward", 0.0))
        if _finite_number(row_reward, "normalized reward", maximum=1.0) != normalized_reward:
            raise VerificationError("Harbor reward differs from the normalized row")
        if (payload.get("exception_info") is not None) != error_present:
            raise VerificationError("Harbor exception state differs from the normalized row")
        expected_snapshot = _row_expected_snapshot(row, snapshots)
        expected_component_hashes = component_hashes_by_snapshot.get(expected_snapshot)
        if expected_component_hashes is None:
            raise VerificationError("trial snapshot lacks independently verified component hashes")
        agent_dir = result_path.parent / "agent"
        trajectory_path = agent_dir / "trajectory.json"
        alternate_atif_path = agent_dir / "atif.json"
        atif_present = any(
            path.exists() or _is_linklike(path)
            for path in (trajectory_path, alternate_atif_path)
        )
        attestation_hash: str | None = None
        failure_receipt_hash: str | None = None
        failure_class: str | None = None
        failure_stage: str | None = None
        mimocode_exit_class: str | None = None
        if atif_present:
            attestation, attestation_hash, llm_call_count = _validate_attestation(
                result_path,
                expected_snapshot,
                expected_component_hashes,
            )
            attestation_hashes.append(attestation_hash)
            rollout_llm_call_count += llm_call_count
            usage = attestation["usage"]
            cost = row.get("cost") or {}
            if not isinstance(cost, dict):
                raise VerificationError("normalized task cost is invalid")
            expected_cost_map = {
                "n_input_tokens": usage["prompt_tokens"],
                "n_cache_tokens": usage["cached_tokens"],
                "n_output_tokens": usage["completion_tokens"],
                "cost_usd": usage["cost_usd"],
            }
            for key, value in expected_cost_map.items():
                if key not in cost or not math.isclose(_finite_number(cost[key], f"row cost {key}"), float(value), abs_tol=1e-9):
                    raise VerificationError(f"normalized task cost differs from attestation: {key}")
            receipt_path = agent_dir / FAILURE_RECEIPT_FILENAME
            if receipt_path.exists() or _is_linklike(receipt_path):
                if not error_present:
                    raise VerificationError("runtime failure receipt requires an errored task row")
                receipt, failure_receipt_hash = _validate_failure_receipt(
                    row,
                    result_path,
                    expected_snapshot,
                    expected_component_hashes,
                    expected_atif_present=True,
                )
                failure_class = receipt["failure_class"]
                failure_stage = receipt["failure_stage"]
                mimocode_exit_class = receipt["mimocode_exit_class"]
                failure_receipt_hashes.append(failure_receipt_hash)
                controlled_receipt_path = _regular_file(
                    receipt_path,
                    root=result_path.parent,
                    max_bytes=64 * 1024,
                )
                if controlled_receipt_path in failure_receipt_paths:
                    raise VerificationError("a runtime failure receipt was reused across trials")
                failure_receipt_paths.add(controlled_receipt_path)
                if attestation.get("runtime_failure_receipt_sha256") != failure_receipt_hash:
                    raise VerificationError("ATIF attestation does not bind its runtime failure receipt")
            elif attestation.get("runtime_failure_receipt_sha256") is not None:
                raise VerificationError("ATIF attestation references a missing runtime failure receipt")
        else:
            if not error_present:
                raise VerificationError("non-errored Harbor trial is missing ATIF evidence")
            receipt_path = agent_dir / FAILURE_RECEIPT_FILENAME
            receipt, failure_receipt_hash = _validate_failure_receipt(
                row,
                result_path,
                expected_snapshot,
                expected_component_hashes,
                expected_atif_present=False,
            )
            controlled_receipt_path = _regular_file(receipt_path, root=result_path.parent, max_bytes=64 * 1024)
            if controlled_receipt_path in failure_receipt_paths:
                raise VerificationError("a runtime failure receipt was reused across trials")
            failure_receipt_paths.add(controlled_receipt_path)
            failure_receipt_hashes.append(failure_receipt_hash)
            missing_atif_receipt_count += 1
            failure_class = receipt["failure_class"]
            failure_stage = receipt["failure_stage"]
            mimocode_exit_class = receipt["mimocode_exit_class"]
            usage = _failure_row_usage(payload, row)
            llm_call_count = 0
        safe_rows.append(
            {
                "task_id": task_id,
                "domain": known_tasks[task_id]["attributes"]["domain"],
                "mode": mode,
                "view_name": row.get("view_name"),
                "evaluation_point_id": row.get("evaluation_point_id"),
                "baseline_role": role,
                "train_batch_index": row.get("train_batch_index"),
                "score": effective_score,
                "success": success,
                "error_present": error_present,
                "runtime_seconds": _optional_number(row.get("runtime_seconds")),
                "input_tokens": usage["prompt_tokens"],
                "output_tokens": usage["completion_tokens"],
                "cached_tokens": usage["cached_tokens"],
                "reasoning_tokens": usage["reasoning_tokens"],
                "cost_usd": usage["cost_usd"],
                "llm_call_count": llm_call_count,
                "snapshot_sha256": expected_snapshot,
                "attestation_sha256": attestation_hash,
                "failure_receipt_sha256": failure_receipt_hash,
                "failure_class": failure_class,
                "failure_stage": failure_stage,
                "mimocode_exit_class": mimocode_exit_class,
                "training_evidence_complete": attestation_hash is not None,
                "harbor_job_result_sha256": _sha256(aggregate_path),
                "harbor_returncode": 0,
                "harbor_result_sha256": _sha256(result_path),
            }
        )
    if actual_counts != expected_counts:
        raise VerificationError(f"task phase counts drifted: {dict(actual_counts)}")
    expected_slot_order = [
        *(
            ("validation", "update_validation", None, 0, "E_0", None, task_id)
            for task_id in splits["val"]
        ),
        *(
            ("train", "train", None, 1, None, None, task_id)
            for task_id in splits["train"][:3]
        ),
        *(
            ("replay", "replay", None, 1, "E_1", "E_1", task_id)
            for task_id in replay
        ),
        *(
            ("train", "train", None, 2, None, None, task_id)
            for task_id in splits["train"][3:]
        ),
        *(
            ("validation", "update_validation", None, 2, "E_2", None, task_id)
            for task_id in splits["val"]
        ),
        *(
            ("replay", "replay", None, 2, "E_2", "E_2", task_id)
            for task_id in replay
        ),
        *(
            ("final", "id_test", "A_T", 2, "E_T", "final", task_id)
            for task_id in splits["test"]
        ),
        *(
            ("final_baseline", "id_test", "A_0", 2, "E_T", "initial", task_id)
            for task_id in splits["test"]
        ),
    ]
    if actual_slot_order != expected_slot_order:
        raise VerificationError("global frozen 24-slot execution order drifted")
    if phase_tasks["train"] != splits["train"]:
        raise VerificationError("train task execution order drifted")
    if phase_tasks["validation"] != splits["val"] + splits["val"]:
        raise VerificationError("frozen-validation execution order drifted")
    if phase_tasks["replay"] != replay + replay:
        raise VerificationError("replay execution order drifted")
    if phase_tasks["final"] != splits["test"] or phase_tasks["final_baseline"] != splits["test"]:
        raise VerificationError("final A0/AT held-out task order drifted")
    if len(attestation_hashes) + missing_atif_receipt_count != 24:
        raise VerificationError("trial ATIF or failure-receipt coverage is incomplete")
    if (
        len(harbor_job_dirs) != 24
        or len(harbor_job_ids) != 24
        or len(harbor_trial_ids) != 24
        or len(harbor_result_paths) != 24
    ):
        raise VerificationError("single-task Harbor job coverage is incomplete")
    jobs_root = (run_dir / "harbor" / "jobs").resolve(strict=True)
    direct_job_root_directories: set[Path] = set()
    support_directories: dict[str, Path] = {}
    for path in jobs_root.iterdir():
        if _is_linklike(path):
            raise VerificationError("Harbor jobs root contains a link-like entry")
        if not path.is_dir():
            raise VerificationError("Harbor jobs root contains an unexpected file")
        resolved = path.resolve(strict=True)
        if path.name in HARBOR_SUPPORT_DIR_NAMES:
            support_directories[path.name] = resolved
        else:
            direct_job_root_directories.add(resolved)
    if direct_job_root_directories != harbor_job_dirs:
        raise VerificationError("Harbor job inventory differs from the frozen 24 slots")
    if set(support_directories) != {"_patched_tasksets"}:
        raise VerificationError("Harbor patched-taskset support inventory drifted")

    expected_job_names = {job_dir.name for job_dir in harbor_job_dirs}
    patched_tasksets_dir = support_directories["_patched_tasksets"]
    patched_entries = list(patched_tasksets_dir.iterdir())
    if any(_is_linklike(path) or not path.is_dir() for path in patched_entries):
        raise VerificationError("Harbor patched-taskset directory contains an unexpected entry")
    if {path.name for path in patched_entries} != expected_job_names or len(patched_entries) != 24:
        raise VerificationError("Harbor patched-taskset inventory differs from the frozen jobs")
    for patched_job in patched_entries:
        expected_task_name = harbor_task_by_job_dir[
            next(job for job in harbor_job_dirs if job.name == patched_job.name)
        ].rsplit("/", 1)[-1]
        patched_children = list(patched_job.iterdir())
        if (
            len(patched_children) != 1
            or _is_linklike(patched_children[0])
            or not patched_children[0].is_dir()
            or patched_children[0].name != expected_task_name
        ):
            raise VerificationError("Harbor patched taskset does not contain exactly its frozen task")
    discovered_aggregates = {
        _regular_file(path, root=jobs_root)
        for path in jobs_root.glob("*/result.json")
    }
    referenced_aggregates = {
        _regular_file(job_dir / "result.json", root=jobs_root)
        for job_dir in harbor_job_dirs
    }
    if discovered_aggregates != referenced_aggregates:
        raise VerificationError("Harbor job inventory differs from the frozen 24 slots")
    summary = _recompute_summary(safe_rows)
    summary["failure_receipt_trials"] = len(failure_receipt_paths)
    summary["missing_atif_failure_receipt_trials"] = missing_atif_receipt_count
    summary["failure_receipt_set_sha256"] = _canonical_sha(sorted(failure_receipt_hashes))
    summary["runtime_failure_classes"] = dict(
        sorted(Counter(row["failure_class"] for row in safe_rows if row["failure_class"] is not None).items())
    )
    summary["runtime_failure_stages"] = dict(
        sorted(Counter(row["failure_stage"] for row in safe_rows if row["failure_stage"] is not None).items())
    )
    summary["mimocode_exit_classes"] = dict(
        sorted(
            Counter(
                row["mimocode_exit_class"]
                for row in safe_rows
                if row["mimocode_exit_class"] is not None
            ).items()
        )
    )
    return safe_rows, summary, rollout_llm_call_count


def _mean(rows: Iterable[dict[str, Any]]) -> float:
    values = [float(row["score"]) for row in rows]
    if not values:
        raise VerificationError("cannot compute a mean from an empty phase")
    return sum(values) / len(values)


def _recompute_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    initial_val = [row for row in rows if row["mode"] == "validation" and row["evaluation_point_id"] == "E_0"]
    final_val = [row for row in rows if row["mode"] == "validation" and row["evaluation_point_id"] != "E_0"]
    at = [row for row in rows if row["baseline_role"] == "A_T"]
    a0 = [row for row in rows if row["baseline_role"] == "A_0"]
    if len(initial_val) != 3 or len(final_val) != 3 or len(at) != 3 or len(a0) != 3:
        raise VerificationError("comparison phases are incomplete")
    a0_by_task = {row["task_id"]: row for row in a0}
    at_by_task = {row["task_id"]: row for row in at}
    if set(a0_by_task) != set(at_by_task):
        raise VerificationError("A0 and AT held-out task sets differ")
    transitions = {
        "0_to_0": 0,
        "0_to_1": 0,
        "1_to_0": 0,
        "1_to_1": 0,
    }
    per_task = []
    for task_id in sorted(a0_by_task):
        before = int(a0_by_task[task_id]["score"])
        after = int(at_by_task[task_id]["score"])
        transitions[f"{before}_to_{after}"] += 1
        per_task.append({"task_id": task_id, "A_0": before, "A_T": after, "delta": after - before})
    a0_mean = _mean(a0)
    at_mean = _mean(at)
    initial_val_mean = _mean(initial_val)
    final_val_mean = _mean(final_val)
    return {
        "held_out": {
            "n_tasks": 3,
            "A_0_mean_score": a0_mean,
            "A_T_mean_score": at_mean,
            "gain_vs_A_0": at_mean - a0_mean,
            "A_0_domain_macro_success_rate": _domain_macro(a0),
            "A_T_domain_macro_success_rate": _domain_macro(at),
            "transitions": transitions,
            "per_task": per_task,
        },
        "frozen_validation": {
            "n_tasks": 3,
            "initial_mean_score": initial_val_mean,
            "final_mean_score": final_val_mean,
            "delta": final_val_mean - initial_val_mean,
        },
        "train_mean_score": _mean(row for row in rows if row["mode"] == "train"),
        "replay_mean_score": _mean(row for row in rows if row["mode"] == "replay"),
        "errors": sum(1 for row in rows if row["error_present"]),
        "rollout_input_tokens": sum(int(row["input_tokens"]) for row in rows),
        "rollout_output_tokens": sum(int(row["output_tokens"]) for row in rows),
        "rollout_cache_tokens": sum(int(row["cached_tokens"]) for row in rows),
        "rollout_reasoning_tokens": sum(int(row["reasoning_tokens"]) for row in rows),
        "rollout_attested_total_tokens": sum(
            int(row["input_tokens"]) + int(row["output_tokens"]) + int(row["reasoning_tokens"])
            for row in rows
        ),
        "seagym_rollout_total_tokens": sum(
            int(row["input_tokens"]) + int(row["cached_tokens"]) + int(row["output_tokens"])
            for row in rows
        ),
        "rollout_cost_usd": sum(float(row["cost_usd"]) for row in rows),
    }


def _domain_macro(rows: Iterable[dict[str, Any]]) -> float:
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        domain = row.get("domain")
        if not isinstance(domain, str) or not domain:
            raise VerificationError("task domain is missing")
        grouped[domain].append(float(row["score"]))
    if not grouped:
        raise VerificationError("cannot compute domain macro from an empty phase")
    return sum(sum(values) / len(values) for values in grouped.values()) / len(grouped)


def _validate_metrics(run_dir: Path, summary: dict[str, Any]) -> dict[str, Any]:
    metrics = _load_json(run_dir / "metrics.json", root=run_dir)
    if not isinstance(metrics, dict):
        raise VerificationError("SEAGym metrics are invalid")
    expected = summary["held_out"]
    for section in ("success_rate", "mean_score"):
        values = metrics.get(section)
        if not isinstance(values, dict):
            raise VerificationError(f"SEAGym metric section missing: {section}")
        if not math.isclose(_finite_number(values.get("id_test.A_0"), section), expected["A_0_mean_score"], abs_tol=1e-12):
            raise VerificationError(f"SEAGym {section} A0 differs from trial evidence")
        if not math.isclose(_finite_number(values.get("id_test.A_T"), section), expected["A_T_mean_score"], abs_tol=1e-12):
            raise VerificationError(f"SEAGym {section} AT differs from trial evidence")
    domain_macro = metrics.get("domain_macro_success_rate")
    if not isinstance(domain_macro, dict):
        raise VerificationError("SEAGym domain-macro metric section is missing")
    if not math.isclose(
        _finite_number(domain_macro.get("id_test.A_0"), "domain macro A0"),
        expected["A_0_domain_macro_success_rate"],
        abs_tol=1e-12,
    ):
        raise VerificationError("SEAGym domain-macro A0 differs from trial evidence")
    if not math.isclose(
        _finite_number(domain_macro.get("id_test.A_T"), "domain macro AT"),
        expected["A_T_domain_macro_success_rate"],
        abs_tol=1e-12,
    ):
        raise VerificationError("SEAGym domain-macro AT differs from trial evidence")
    final_gain = metrics.get("final_gain")
    if not isinstance(final_gain, dict) or not math.isclose(_finite_number(final_gain.get("id_test"), "final gain", minimum=-1.0, maximum=1.0), expected["gain_vs_A_0"], abs_tol=1e-12):
        raise VerificationError("SEAGym final gain differs from trial evidence")
    tokens = metrics.get("tokens")
    if not isinstance(tokens, dict):
        raise VerificationError("SEAGym token metrics are missing")
    rollout = tokens.get("rollout")
    update = tokens.get("update")
    overall = tokens.get("overall")
    if not isinstance(rollout, dict) or rollout.get("num_records") != 24:
        raise VerificationError("SEAGym rollout token record count drifted")
    if not isinstance(update, dict) or update.get("num_records") != 2:
        raise VerificationError("SEAGym update token record count drifted")
    if not isinstance(overall, dict) or overall.get("num_records") != 26:
        raise VerificationError("SEAGym overall token record count drifted")
    for group in (rollout, update, overall):
        for key in ("input_tokens", "output_tokens", "total_tokens", "cost_usd"):
            if key in group:
                _finite_number(group[key], f"SEAGym tokens.{key}")
    if not math.isclose(
        _finite_number(rollout.get("total_tokens"), "rollout total tokens"),
        summary["seagym_rollout_total_tokens"],
        abs_tol=1e-6,
    ):
        raise VerificationError("SEAGym rollout token total differs from trial attestations")
    token_field_expectations = {
        "input_tokens": summary["rollout_input_tokens"],
        "cache_tokens": summary["rollout_cache_tokens"],
        "output_tokens": summary["rollout_output_tokens"],
    }
    for field, expected_total in token_field_expectations.items():
        if not math.isclose(_finite_number(rollout.get(field), f"rollout {field}"), expected_total, abs_tol=1e-6):
            raise VerificationError(f"SEAGym rollout {field} differs from trial attestations")
    if not math.isclose(_finite_number(rollout.get("cost_usd"), "rollout cost"), summary["rollout_cost_usd"], abs_tol=1e-8):
        raise VerificationError("SEAGym rollout cost differs from trial attestations")
    return {
        "rollout_attested_total_tokens": summary["rollout_attested_total_tokens"],
        "seagym_rollout_total_tokens": rollout["total_tokens"],
        "seagym_update_total_tokens": update.get("total_tokens", 0),
        "seagym_overall_total_tokens": overall["total_tokens"],
        "rollout_reasoning_tokens": summary["rollout_reasoning_tokens"],
        "token_accounting_note": "Attested totals include bounded provider-reported reasoning-token usage; SEAGym total_tokens separately adds cache_tokens to cache-inclusive Harbor input_tokens and excludes reasoning telemetry. Both conventions are retained.",
        "rollout_cost_usd": rollout["cost_usd"],
        "update_cost_usd": update.get("cost_usd", 0),
        "overall_cost_usd": overall["cost_usd"],
    }


def _validate_evaluation_points(run_dir: Path, summary: dict[str, Any]) -> None:
    points = _load_jsonl(run_dir / "records" / "evaluation_points.jsonl", root=run_dir, expected=4)
    if [point.get("evaluation_point_id") for point in points] != ["E_0", "E_1", "E_2", "E_T"]:
        raise VerificationError("SEAGym evaluation-point sequence drifted")
    final = points[-1]
    evaluations = final.get("evaluations")
    view = evaluations.get("id_test") if isinstance(evaluations, dict) else None
    if not isinstance(view, dict) or view.get("agent_checkpoint_id") != "A_T" or view.get("baseline_checkpoint_id") != "A_0":
        raise VerificationError("SEAGym final checkpoint comparison is invalid")
    held_out = summary["held_out"]
    expected = {
        "num_tasks": 3,
        "num_baseline_tasks": 3,
        "score": held_out["A_T_mean_score"],
        "baseline_score": held_out["A_0_mean_score"],
        "gain_vs_A_0": held_out["gain_vs_A_0"],
    }
    for key, value in expected.items():
        if isinstance(value, int):
            if view.get(key) != value:
                raise VerificationError(f"final evaluation point differs: {key}")
        elif not math.isclose(_finite_number(view.get(key), f"final point {key}", minimum=-1.0, maximum=1.0), value, abs_tol=1e-12):
            raise VerificationError(f"final evaluation point differs: {key}")


def _proxy_counter(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VerificationError(f"guard-proxy {label} is not a non-negative integer")
    return value


def _validate_guard_proxy_health(
    run_dir: Path,
    *,
    minimum_logical_requests: int,
    maximum_logical_requests: int,
) -> dict[str, Any]:
    health = _load_json(
        run_dir / "evidence" / "guard-proxy-health.json",
        root=run_dir,
    )
    expected_keys = {
        "active_requests",
        "completed_requests",
        "credential_persisted",
        "forwarded_requests",
        "guard_proxy_source_sha256",
        "limits",
        "max_upstream_retries",
        "normalizations",
        "ready",
        "rejected_requests",
        "rejection_classes",
        "remaining_requests",
        "request_profiles",
        "request_limit",
        "root_session_binding_enabled",
        "root_session_rejections",
        "root_sessions_limit",
        "root_sessions_observed",
        "retry_policy",
        "schema_version",
        "upstream_attempt_error_classes",
        "upstream_attempts",
        "upstream_error_classes",
        "upstream_errors",
        "upstream_http_statuses",
        "upstream_retries",
    }
    if not isinstance(health, dict) or set(health) != expected_keys:
        raise VerificationError("guard-proxy health schema drifted")
    if (
        health.get("schema_version") != EXPECTED_GUARD_PROXY_RUNTIME["health_schema_version"]
        or health.get("guard_proxy_source_sha256") != EXPECTED_GUARD_PROXY_RUNTIME["source_sha256"]
        or health.get("limits") != EXPECTED_GUARD_PROXY_RUNTIME["limits"]
        or health.get("retry_policy") != EXPECTED_RETRY_POLICY
        or health.get("max_upstream_retries") != EXPECTED_RETRY_POLICY["max_retries_per_client_request"]
    ):
        raise VerificationError("guard-proxy runtime identity or retry policy drifted")
    if health.get("ready") is not True or health.get("credential_persisted") is not False:
        raise VerificationError("guard-proxy readiness or credential boundary failed")

    active = _proxy_counter(health.get("active_requests"), "active_requests")
    forwarded = _proxy_counter(health.get("forwarded_requests"), "forwarded_requests")
    completed = _proxy_counter(health.get("completed_requests"), "completed_requests")
    rejected = _proxy_counter(health.get("rejected_requests"), "rejected_requests")
    upstream_errors = _proxy_counter(health.get("upstream_errors"), "upstream_errors")
    upstream_attempts = _proxy_counter(health.get("upstream_attempts"), "upstream_attempts")
    upstream_retries = _proxy_counter(health.get("upstream_retries"), "upstream_retries")
    request_limit = _proxy_counter(health.get("request_limit"), "request_limit")
    remaining_requests = _proxy_counter(health.get("remaining_requests"), "remaining_requests")
    root_binding = EXPECTED_GUARD_PROXY_RUNTIME["root_session_binding"]
    root_session_rejections = _proxy_counter(
        health.get("root_session_rejections"),
        "root_session_rejections",
    )
    root_sessions_limit = _proxy_counter(
        health.get("root_sessions_limit"),
        "root_sessions_limit",
    )
    root_sessions_observed = _proxy_counter(
        health.get("root_sessions_observed"),
        "root_sessions_observed",
    )
    if (
        active != 0
        or forwarded <= 0
        or not minimum_logical_requests <= forwarded <= maximum_logical_requests
        or completed != forwarded
        or rejected != 0
        or upstream_errors != 0
        or request_limit != EXPECTED_GUARD_PROXY_RUNTIME["limits"]["max_requests"]
        or remaining_requests != request_limit - forwarded
        or upstream_attempts != forwarded + upstream_retries
        or upstream_retries > forwarded * EXPECTED_RETRY_POLICY["max_retries_per_client_request"]
        or health.get("root_session_binding_enabled") is not True
        or root_session_rejections != 0
        or root_sessions_limit != root_binding["full_pilot_limit"]
        or root_sessions_observed != root_binding["full_pilot_limit"]
    ):
        raise VerificationError("guard-proxy completed-run counters are inconsistent")

    rejection_classes = health.get("rejection_classes")
    if rejection_classes != {"concurrency_limit": 0, "request_limit": 0, "other": 0}:
        raise VerificationError("guard-proxy recorded a rejected logical request")
    final_error_classes = health.get("upstream_error_classes")
    if final_error_classes != {name: 0 for name in PROXY_ERROR_CLASSES}:
        raise VerificationError("guard-proxy recorded a final upstream request error")

    normalizations = health.get("normalizations")
    if not isinstance(normalizations, dict) or set(normalizations) != {"tool_choice_none_to_no_tools"}:
        raise VerificationError("guard-proxy normalization counters drifted")
    none_normalizations = _proxy_counter(
        normalizations["tool_choice_none_to_no_tools"],
        "normalizations.tool_choice_none_to_no_tools",
    )
    profiles = health.get("request_profiles")
    expected_profile_fields = {
        "inbound_tool_choice",
        "outbound_tool_choice",
        "final_upstream_errors_by_outbound_tool_choice",
    }
    if not isinstance(profiles, dict) or set(profiles) != expected_profile_fields:
        raise VerificationError("guard-proxy request-profile schema drifted")
    safe_profiles: dict[str, dict[str, int]] = {}
    for field in expected_profile_fields:
        profile = profiles[field]
        if not isinstance(profile, dict) or set(profile) != set(PROXY_TOOL_CHOICE_BUCKETS):
            raise VerificationError("guard-proxy request-profile buckets drifted")
        safe_profiles[field] = {
            bucket: _proxy_counter(profile[bucket], f"request_profiles.{field}.{bucket}")
            for bucket in PROXY_TOOL_CHOICE_BUCKETS
        }
    inbound_profile = safe_profiles["inbound_tool_choice"]
    outbound_profile = safe_profiles["outbound_tool_choice"]
    final_error_profile = safe_profiles["final_upstream_errors_by_outbound_tool_choice"]
    if (
        sum(inbound_profile.values()) != forwarded
        or sum(outbound_profile.values()) != forwarded
        or sum(final_error_profile.values()) != upstream_errors
        or none_normalizations > inbound_profile["none"]
        or outbound_profile["none"] != inbound_profile["none"] - none_normalizations
        or outbound_profile["absent"] != inbound_profile["absent"] + none_normalizations
        or any(outbound_profile[bucket] != inbound_profile[bucket] for bucket in ("auto", "required", "named"))
    ):
        raise VerificationError("guard-proxy request-profile counters are inconsistent")

    attempt_error_classes = health.get("upstream_attempt_error_classes")
    statuses = health.get("upstream_http_statuses")
    if not isinstance(attempt_error_classes, dict) or set(attempt_error_classes) != set(PROXY_ERROR_CLASSES):
        raise VerificationError("guard-proxy attempt-error classes drifted")
    if not isinstance(statuses, dict) or set(statuses) != set(PROXY_HTTP_STATUS_BUCKETS):
        raise VerificationError("guard-proxy HTTP status buckets drifted")
    safe_attempt_errors = {
        name: _proxy_counter(attempt_error_classes[name], f"upstream_attempt_error_classes.{name}")
        for name in PROXY_ERROR_CLASSES
    }
    safe_statuses = {
        name: _proxy_counter(statuses[name], f"upstream_http_statuses.{name}")
        for name in PROXY_HTTP_STATUS_BUCKETS
    }
    retryable_statuses = {str(value) for value in EXPECTED_RETRY_POLICY["retryable_http_statuses"]}
    if any(count for name, count in safe_statuses.items() if name not in retryable_statuses):
        raise VerificationError("guard-proxy retried or completed with a non-retryable HTTP status")
    four_xx = sum(safe_statuses[str(value)] for value in (404, 408, 409, 425, 429))
    five_xx = sum(safe_statuses[str(value)] for value in (500, 502, 503, 504, 524, 529))
    expected_attempt_classes = {name: 0 for name in PROXY_ERROR_CLASSES}
    expected_attempt_classes["http_4xx"] = four_xx
    expected_attempt_classes["http_5xx"] = five_xx
    if (
        safe_attempt_errors != expected_attempt_classes
        or sum(safe_statuses.values()) != upstream_retries
        or sum(safe_attempt_errors.values()) != upstream_retries
    ):
        raise VerificationError("guard-proxy retry attempt counters are inconsistent")
    return health


def _usage_cost(before_path: Path, after_path: Path) -> dict[str, Any]:
    before = _load_json(before_path)
    after = _load_json(after_path)
    for label, value in (("before", before), ("after", after)):
        if not isinstance(value, dict) or value.get("schema_version") != "openrouter-safe-key-usage-v1" or value.get("authenticated") is not True:
            raise VerificationError(f"OpenRouter {label} usage evidence is invalid")
        numeric = value.get("numeric")
        if not isinstance(numeric, dict):
            raise VerificationError(f"OpenRouter {label} numeric usage is missing")
    try:
        before_usage = Decimal(str(before["numeric"]["usage"]))
        after_usage = Decimal(str(after["numeric"]["usage"]))
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise VerificationError("OpenRouter cumulative usage is invalid") from exc
    if not before_usage.is_finite() or not after_usage.is_finite() or before_usage < 0 or after_usage < before_usage:
        raise VerificationError("OpenRouter cumulative usage moved backwards or is non-finite")
    delta = after_usage - before_usage
    if delta > Decimal("1.20"):
        raise VerificationError("observed OpenRouter usage exceeded the authorized USD 1.20 maximum")
    return {
        "observed_key_usage_delta_usd": float(delta),
        "before_checked_at": before.get("checked_at"),
        "after_checked_at": after.get("checked_at"),
        "accounting_scope": "entire_key_between_two_timestamps",
    }


def _classification(summary: dict[str, Any]) -> str:
    gain = summary["held_out"]["gain_vs_A_0"]
    validation_delta = summary["frozen_validation"]["delta"]
    if summary["errors"] or gain < 0 or validation_delta < 0:
        return "negative_pilot_signal"
    if gain > 0:
        return "positive_pilot_signal"
    return "no_detectable_pilot_signal"


def verify_pilot(
    *,
    run_dir: Path,
    protocol_path: Path,
    usage_before: Path,
    usage_after: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    run_dir = run_dir.resolve(strict=True)
    if run_dir.is_symlink() or not run_dir.is_dir():
        raise VerificationError("run_dir must be a regular directory")
    protocol, repo_root = _validate_protocol(protocol_path)
    _config, split, task_index = _validate_frozen_inputs(protocol, repo_root, run_dir)
    plan = _validate_batch_plan(run_dir, split)
    initial_a0, initial_candidate, initial_components = _checkpoint_snapshot(run_dir, "initial")
    if initial_a0 != initial_candidate:
        raise VerificationError("initial A0 checkpoint is not the unevolved snapshot")
    e1_a0, e1, e1_components = _checkpoint_snapshot(run_dir, "E_1")
    final_a0, final_candidate, final_components = _checkpoint_snapshot(run_dir, "final")
    if not (initial_a0 == e1_a0 == final_a0):
        raise VerificationError("A0 snapshot changed across checkpoints")
    component_hashes_by_snapshot: dict[str, dict[str, str]] = {}
    for source in (initial_components, e1_components, final_components):
        for digest, component_hashes in source.items():
            previous = component_hashes_by_snapshot.get(digest)
            if previous is not None and previous != component_hashes:
                raise VerificationError("snapshot component hashes conflict across checkpoints")
            component_hashes_by_snapshot[digest] = component_hashes
    updates, candidate_by_update = _validate_updates(run_dir)
    if candidate_by_update.get(1) != e1 or candidate_by_update.get(2) != final_candidate:
        raise VerificationError("update candidate hashes differ from checkpoints")
    expected_before = initial_candidate
    for index, (update, candidate) in enumerate(zip(updates, (e1, final_candidate), strict=True), start=1):
        changed = update["changed"] is True
        if changed == (candidate == expected_before):
            raise VerificationError(f"update {index} changed flag disagrees with snapshot lineage")
        expected_before = candidate
    snapshots = {"A0": initial_a0, "E1": e1, "AT": final_candidate}
    safe_rows, summary, rollout_llm_call_count = _validate_rows(
        run_dir,
        split,
        plan,
        task_index,
        snapshots,
        component_hashes_by_snapshot,
    )
    for update in updates:
        index = update["update_index"]
        batch_rows = [
            row
            for row in safe_rows
            if row["mode"] == "train" and row["train_batch_index"] == index
        ]
        all_missing_atif = len(batch_rows) == 3 and all(
            row["training_evidence_complete"] is False for row in batch_rows
        )
        if update["model_call_executed"] is False and not all_missing_atif:
            raise VerificationError("ATIF skip does not correspond to an all-receipted error batch")
        if update["model_call_executed"] is True and all_missing_atif:
            raise VerificationError("update model was called for a batch with no usable ATIF evidence")
    metric_usage = _validate_metrics(run_dir, summary)
    _validate_evaluation_points(run_dir, summary)
    update_llm_call_count = sum(update["model_call_executed"] is True for update in updates)
    skipped_update_count = sum(update["model_call_executed"] is False for update in updates)
    failure_receipt_trials = int(summary["failure_receipt_trials"])
    missing_atif_failure_receipt_trials = int(summary["missing_atif_failure_receipt_trials"])
    proxy_health = _validate_guard_proxy_health(
        run_dir,
        minimum_logical_requests=rollout_llm_call_count,
        maximum_logical_requests=rollout_llm_call_count + missing_atif_failure_receipt_trials * 32,
    )
    verified_rollout_logical_requests = int(proxy_health["forwarded_requests"])
    verified_total_logical_requests = verified_rollout_logical_requests + update_llm_call_count
    cost = _usage_cost(usage_before, usage_after)
    result = {
        "schema_version": FORMAT_VERSION,
        "claim": CLAIM,
        "protocol_id": protocol["protocol_id"],
        "run_id": plan["run_id"],
        "experiment_id": plan["experiment_id"],
        "results_status": (
            "completed_with_incomplete_training_evidence"
            if failure_receipt_trials or skipped_update_count
            else "verified_completed_real_pilot"
        ),
        "pilot_kind": "real_external_scientific_pilot",
        "leaderboard_submission": False,
        "paper_scale_reproduction": False,
        "directional_only": True,
        "held_out_n": 3,
        "seed": 42,
        "model": {
            "request_id": EXPECTED_MODEL_API,
            "canonical_id": EXPECTED_CANONICAL_MODEL,
            "harbor_id": EXPECTED_MODEL_HARBOR,
            "provider_endpoint": EXPECTED_ENDPOINT,
            "fallbacks_allowed": False,
            "reasoning_enabled": False,
            "update_model_seed_parameter_sent": False,
            "provider_update_sampling_determinism_claimed": False,
            "provider_rollout_sampling_determinism_claimed": False,
        },
        "upstream": EXPECTED_UPSTREAM,
        "snapshots": snapshots,
        "comparison": summary,
        "classification": _classification(summary),
        "usage": {**metric_usage, **cost},
        "evidence": {
            "planned_task_trials": 24,
            "verified_task_trials": len(safe_rows),
            "verified_update_attempts": len(updates),
            "verified_rollout_model_calls": verified_rollout_logical_requests,
            "attested_rollout_model_calls": rollout_llm_call_count,
            "runtime_failure_receipt_trials": failure_receipt_trials,
            "missing_atif_failure_receipt_trials": missing_atif_failure_receipt_trials,
            "verified_update_model_calls": update_llm_call_count,
            "skipped_update_attempts": skipped_update_count,
            "verified_guard_proxy_logical_requests": verified_rollout_logical_requests,
            "verified_total_logical_model_requests": verified_total_logical_requests,
            "privacy_projection_verified": True,
            "credential_exposure_observed": False,
            "hidden_reasoning_persisted": False,
            "guard_proxy_health": proxy_health,
        },
        "claim_boundary": {
            "causal_attribution_claimed": False,
            "automatic_promotion": False,
            "promotion_effect": "none",
            "official_terminal_bench_leaderboard_result": False,
        },
    }
    return result, safe_rows, updates


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def write_bundle(output_dir: Path, result: dict[str, Any], rows: list[dict[str, Any]], updates: list[dict[str, Any]]) -> None:
    output_dir = output_dir.resolve(strict=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink():
        raise VerificationError("output_dir cannot be a symlink")
    allowed_existing = {"comparison.json", "task-results.jsonl", "update-summary.json", "SHA256SUMS"}
    unexpected = {path.name for path in output_dir.iterdir()} - allowed_existing
    if unexpected:
        raise VerificationError("output_dir contains unexpected files")
    _atomic_json(output_dir / "comparison.json", result)
    _atomic_jsonl(output_dir / "task-results.jsonl", rows)
    _atomic_json(output_dir / "update-summary.json", {"schema_version": FORMAT_VERSION, "updates": updates})
    names = ["comparison.json", "task-results.jsonl", "update-summary.json"]
    lines = [f"{_sha256(output_dir / name)}  {name}" for name in names]
    (output_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    for name in names:
        _scan_secret_bytes(output_dir / name)
        value = _load_json(output_dir / name) if name.endswith(".json") else None
        if value is not None:
            _reject_nonempty_reasoning(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--openrouter-usage-before", type=Path, required=True)
    parser.add_argument("--openrouter-usage-after", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result, rows, updates = verify_pilot(
            run_dir=args.run_dir,
            protocol_path=args.protocol,
            usage_before=args.openrouter_usage_before,
            usage_after=args.openrouter_usage_after,
        )
        write_bundle(args.output_dir, result, rows, updates)
    except (OSError, VerificationError) as exc:
        raise SystemExit(f"SEAGym pilot verification failed: {type(exc).__name__}: {exc}") from None
    print(f"Verified {len(rows)} real Terminal-Bench task trials; classification={result['classification']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
