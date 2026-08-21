from evoagent.model_registry import (
    build_training_authorization_reference,
    canonical_sha256,
    normalize_training_authorization_scope,
)


CANDIDATE_ID = "synthetic/candidate-v1"
PACKAGE_HASH = "a" * 64
EXPECTED_SCOPE = {
    "scope_version": "evoagent-training-authorization-v1",
    "candidate_id": CANDIDATE_ID,
    "training_intent_package_hash": PACKAGE_HASH,
}


def _reference(payload):
    return build_training_authorization_reference(
        reference_id="authorization-reference-v1",
        signer_identity="external-authority",
        external_verification_uri="synthetic://authorization/reference-v1",
        authorization_payload=payload,
    )


def test_known_private_v1_scope_spellings_normalize_to_one_hash():
    payloads = (
        {
            "candidate_id": CANDIDATE_ID,
            "training_intent_package_hash": PACKAGE_HASH,
        },
        {
            "candidate_id": CANDIDATE_ID,
            "training_intent_package_hash": PACKAGE_HASH,
            "synthetic_fixture": True,
        },
        {
            "candidate_id": CANDIDATE_ID,
            "training_intent_package_hash": PACKAGE_HASH,
            "maximum_rollouts": 64,
            "synthetic_fixture": True,
        },
        {
            "candidate_id": CANDIDATE_ID,
            "base_model_id": "synthetic/base-v1",
            "training_intent_package_hash": PACKAGE_HASH,
            "training_intent_campaign_id": "campaign:model-v1",
            "training_method": "agentic_rl",
            "evidence_manifest_hash": "b" * 64,
            "held_out_task_ids": ("held-out-a", "held-out-b"),
            "maximum_budget": {"max_rollouts": 64},
            "source_commit": "c" * 40,
        },
    )

    expected_hash = canonical_sha256(EXPECTED_SCOPE)
    for payload in payloads:
        assert normalize_training_authorization_scope(payload) == EXPECTED_SCOPE
        assert _reference(payload).authorization_hash == expected_hash


def test_candidate_and_complete_package_hash_remain_authoritative():
    wrong_candidate = {
        "candidate_id": "synthetic/another-candidate-v1",
        "training_intent_package_hash": PACKAGE_HASH,
    }
    wrong_package = {
        "candidate_id": CANDIDATE_ID,
        "training_intent_package_hash": "d" * 64,
    }

    expected_hash = canonical_sha256(EXPECTED_SCOPE)
    assert _reference(wrong_candidate).authorization_hash != expected_hash
    assert _reference(wrong_package).authorization_hash != expected_hash


def test_unknown_scope_shape_is_not_silently_normalized():
    payload = {
        "candidate_id": CANDIDATE_ID,
        "training_intent_package_hash": PACKAGE_HASH,
        "unrecognized_permission": "deploy",
    }

    assert normalize_training_authorization_scope(payload) == payload
    assert _reference(payload).authorization_hash == canonical_sha256(payload)
    assert _reference(payload).authorization_hash != canonical_sha256(EXPECTED_SCOPE)
