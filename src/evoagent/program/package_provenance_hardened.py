from __future__ import annotations

from evoagent.program.constraints import validate_hardened_program_policy
from evoagent.program.package import (
    EvolutionProgramPackageError,
    EvolutionProgramPackageManifest,
    ProgramControlEvidence,
)
from evoagent.program.package_audit_hardened import (
    AuditHardenedEvolutionProgramPackageManager as _AuditHardenedManager,
)


class AuditHardenedEvolutionProgramPackageManager(_AuditHardenedManager):
    """Final public Package Manager with provenance, time and role binding."""

    def verify(self, manifest: EvolutionProgramPackageManifest) -> bool:
        super().verify(manifest)
        self._verify_hardened_policies(manifest)
        self._verify_source_identity(manifest)
        self._verify_causal_chronology(manifest)
        self._verify_evaluator_role_separation(manifest)
        return True

    @staticmethod
    def _verify_hardened_policies(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        for label, policy in (
            ("main", manifest.policy),
            ("budget control", manifest.budget_control.policy),
            ("ambiguous control", manifest.ambiguous_control.policy),
        ):
            try:
                validate_hardened_program_policy(policy)
            except ValueError as exc:
                raise EvolutionProgramPackageError(
                    f"Packaged {label} Program policy disables a required safeguard."
                ) from exc

    @staticmethod
    def _verify_source_identity(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        releases = (
            manifest.drift_release_package,
            manifest.passing_release_package,
        )
        for field in (
            "framework_version",
            "source_repository",
            "source_commit",
            "third_party_lock_hash",
        ):
            expected = getattr(manifest, field)
            if any(getattr(release, field) != expected for release in releases):
                raise EvolutionProgramPackageError(
                    f"Program package {field} differs from embedded "
                    "release provenance."
                )
        if any(
            release.plan.source_commit != manifest.source_commit
            for release in releases
        ):
            raise EvolutionProgramPackageError(
                "Program package source commit differs from embedded "
                "ReleasePlan."
            )

    @classmethod
    def _verify_causal_chronology(
        cls,
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if (
            len(manifest.generations) != 2
            or len(manifest.decisions) != 2
            or len(manifest.program_events) != 12
            or len(manifest.campaign_events) != 7
        ):
            raise EvolutionProgramPackageError(
                "Program chronology requires complete main lifecycles."
            )
        g0, g1 = manifest.generations
        d0, d1 = manifest.decisions
        if g0.outcome is None or g1.outcome is None or g1.plan is None:
            raise EvolutionProgramPackageError(
                "Program chronology requires complete generation evidence."
            )

        release_ready_at = max(
            manifest.drift_release_package.created_at,
            manifest.passing_release_package.created_at,
        )
        program_times = tuple(
            event.created_at for event in manifest.program_events
        )
        campaign_times = tuple(
            event.created_at for event in manifest.campaign_events
        )
        if program_times != tuple(sorted(program_times)):
            raise EvolutionProgramPackageError(
                "Program audit chronology is not monotonic."
            )
        if campaign_times != tuple(sorted(campaign_times)):
            raise EvolutionProgramPackageError(
                "Generation Campaign audit chronology is not monotonic."
            )

        timeline = (
            release_ready_at,
            g0.outcome.completed_at,
            g0.created_at,
            manifest.signal.created_at,
            manifest.attribution.created_at,
            d0.decided_at,
            g1.plan.created_at,
            g1.created_at,
            manifest.campaign_events[0].created_at,
            manifest.campaign_events[3].created_at,
            manifest.program_events[6].created_at,
            manifest.campaign_events[4].created_at,
            manifest.campaign_events[5].created_at,
            manifest.program_events[7].created_at,
            manifest.program_events[8].created_at,
            g1.outcome.completed_at,
            g1.updated_at,
            manifest.program_events[9].created_at,
            manifest.campaign_events[6].created_at,
            d1.decided_at,
            manifest.final_head.updated_at,
            manifest.created_at,
        )
        if timeline != tuple(sorted(timeline)):
            raise EvolutionProgramPackageError(
                "Program causal chronology is not monotonic from release "
                "evidence to package."
            )
        if (
            g0.created_at != g0.outcome.completed_at
            or manifest.program_events[0].created_at != g0.created_at
            or manifest.program_events[1].created_at != g0.created_at
        ):
            raise EvolutionProgramPackageError(
                "Observed Generation 0 time differs from its release outcome."
            )
        if (
            manifest.program_events[5].created_at != g1.created_at
            or manifest.program_events[5].created_at
            != manifest.program_events[6].created_at
        ):
            raise EvolutionProgramPackageError(
                "Generation submission records do not share one exact "
                "submission time."
            )
        if (
            g1.updated_at != g1.outcome.completed_at
            or manifest.program_events[9].created_at
            != g1.outcome.completed_at
            or manifest.campaign_events[6].created_at
            != g1.outcome.completed_at
        ):
            raise EvolutionProgramPackageError(
                "Generation completion records do not share one exact "
                "completion time."
            )
        if (
            manifest.final_head.updated_at != d1.decided_at
            or manifest.program_events[10].created_at != d1.decided_at
            or manifest.program_events[11].created_at != d1.decided_at
        ):
            raise EvolutionProgramPackageError(
                "Final Program decision time differs from terminal state."
            )
        if (
            manifest.generation_campaign.created_at
            != manifest.campaign_events[0].created_at
            or manifest.generation_campaign.updated_at
            != manifest.campaign_events[6].created_at
        ):
            raise EvolutionProgramPackageError(
                "Generation Campaign record time differs from its audit "
                "lifecycle."
            )
        if any(
            event.created_at > manifest.created_at
            for event in (
                *manifest.program_events,
                *manifest.campaign_events,
            )
        ):
            raise EvolutionProgramPackageError(
                "Program or Campaign audit event occurs after package creation."
            )

        cls._verify_control_chronology(
            manifest.budget_control,
            release_ready_at=release_ready_at,
            package_created_at=manifest.created_at,
        )
        cls._verify_control_chronology(
            manifest.ambiguous_control,
            release_ready_at=release_ready_at,
            package_created_at=manifest.created_at,
        )

    @staticmethod
    def _verify_control_chronology(
        control: ProgramControlEvidence,
        *,
        release_ready_at,
        package_created_at,
    ) -> None:
        if (
            len(control.generations) != 1
            or len(control.decisions) != 1
            or not control.signals
        ):
            raise EvolutionProgramPackageError(
                "Program control chronology lacks immutable evidence."
            )
        generation = control.generations[0]
        decision = control.decisions[0]
        if generation.outcome is None:
            raise EvolutionProgramPackageError(
                "Program control chronology lacks Generation 0 outcome."
            )
        event_times = tuple(event.created_at for event in control.events)
        if event_times != tuple(sorted(event_times)):
            raise EvolutionProgramPackageError(
                "Program control audit chronology is not monotonic."
            )
        evidence_times = [
            release_ready_at,
            generation.outcome.completed_at,
            generation.created_at,
            control.signals[0].created_at,
        ]
        evidence_times.extend(
            item.created_at for item in control.attributions
        )
        evidence_times.extend(
            (
                decision.decided_at,
                control.final_head.updated_at,
                package_created_at,
            )
        )
        if tuple(evidence_times) != tuple(sorted(evidence_times)):
            raise EvolutionProgramPackageError(
                "Program control causal chronology is not monotonic."
            )
        if (
            generation.created_at != generation.outcome.completed_at
            or control.final_head.updated_at != decision.decided_at
            or control.events[-1].created_at != decision.decided_at
            or any(
                event.created_at > package_created_at
                for event in control.events
            )
        ):
            raise EvolutionProgramPackageError(
                "Program control record time differs from its audit lifecycle."
            )

    @staticmethod
    def _verify_evaluator_role_separation(
        manifest: EvolutionProgramPackageManifest,
    ) -> None:
        if (
            len(manifest.campaign_events) != 7
            or len(manifest.program_events) != 12
        ):
            raise EvolutionProgramPackageError(
                "Program role verification requires complete audit lifecycles."
            )
        evaluation_actors = {
            manifest.campaign_events[index].actor_id
            for index in (1, 2, 3)
        }
        approval_actors = {
            item.actor_id for item in manifest.generation_approvals
        }
        program_execution_actors = {
            manifest.program_events[index].actor_id
            for index in (7, 8, 9)
        }
        campaign_completion_actor = (
            manifest.campaign_events[6].actor_id
        )
        if len(evaluation_actors) != 1:
            raise EvolutionProgramPackageError(
                "Controlled Generation Campaign requires one exact "
                "independent evaluator."
            )
        evaluator = next(iter(evaluation_actors))
        if manifest.program_events[6].actor_id != evaluator:
            raise EvolutionProgramPackageError(
                "Generation Campaign binding actor differs from evaluator."
            )
        if evaluation_actors & approval_actors:
            raise EvolutionProgramPackageError(
                "Generation evaluator also approved its own Campaign."
            )
        if evaluation_actors & program_execution_actors:
            raise EvolutionProgramPackageError(
                "Generation evaluator also authorized or executed the "
                "generation."
            )
        if campaign_completion_actor == evaluator:
            raise EvolutionProgramPackageError(
                "Generation evaluator also completed the Generation Campaign."
            )
        if campaign_completion_actor != manifest.program_events[9].actor_id:
            raise EvolutionProgramPackageError(
                "Generation Campaign completion actor differs from Generation "
                "completion."
            )


__all__ = ["AuditHardenedEvolutionProgramPackageManager"]
