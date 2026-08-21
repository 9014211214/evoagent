# Core License Decision

## Decision

The repository owner has explicitly authorized completion of the remaining open-source release work. The independently authored core is licensed under the **Apache License, Version 2.0 (SPDX: Apache-2.0)**, with the full license text stored in the root `LICENSE` file.

This decision applies only to independently authored project code and documentation. It does not relicense third-party projects, benchmark assets, model weights, datasets, generated artifacts, or external services.

## Why Apache-2.0

Apache-2.0 is appropriate for this infrastructure-style framework because it permits commercial and non-commercial use, modification, and redistribution while also providing an express patent license and preserving attribution/license obligations.

## Owner-side legal confirmation

On 2026-08-20 the owner confirmed authority to publish the independently authored core under Apache-2.0 and confirmed that no applicable employment, invention-assignment, confidentiality, or organizational open-source conflict prohibits publication. Export, privacy, trademark, and third-party rights remain subject to their ordinary independent obligations.

## Third-party separation

`THIRD_PARTY_LOCK.json` and `THIRD_PARTY_NOTICES.md` remain the source of truth for reviewed third-party integrations. Where an upstream project does not publish an explicit license at the pinned revision, this repository does not copy or redistribute that upstream source or assets and uses only independently authored interoperability code and documented observable file formats until the upstream licensing status is clarified.
