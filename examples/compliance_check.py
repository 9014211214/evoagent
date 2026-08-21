from pathlib import Path

from evoagent.compliance import ThirdPartyComplianceVerifier

root = Path(__file__).resolve().parents[1]
result = ThirdPartyComplianceVerifier().verify(
    lock_path=root / "THIRD_PARTY_LOCK.json",
    notices_path=root / "THIRD_PARTY_NOTICES.md",
)

print("components verified:", result.components_verified)
print("lock hash:", result.lock_hash)
print("verified:", result.verified)
