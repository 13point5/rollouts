from __future__ import annotations

from dataclasses import dataclass

from rollouts.commands.learn import get_learn_run_for_session, record_prime_model_id_for_learn_run
from rollouts.commands.opencode import OpenCodeConfigUpdateResult, update_global_opencode_model
from rollouts.commands.prime import (
    PrimeAdapterResult,
    ensure_prime_run_adapter_deployed,
    get_prime_inference_model_id,
)
from rollouts.errors import RolloutsError
from rollouts.models import LearnRunRecord, LearnSessionRecord


@dataclass(frozen=True)
class LearnUseResult:
    session: LearnSessionRecord
    run: LearnRunRecord
    adapter: PrimeAdapterResult
    model_id: str
    opencode: OpenCodeConfigUpdateResult


def use_learn_run_in_opencode(
    *,
    session_name: str,
    run_number: int | None = None,
) -> LearnUseResult:
    session_status, run = get_learn_run_for_session(
        session_name=session_name,
        run_number=run_number,
    )
    if run.prime_run_id is None:
        raise RolloutsError(f"learn run #{run.run_number} does not have a Prime run id recorded")

    adapter = ensure_prime_run_adapter_deployed(run_id=run.prime_run_id)
    model_id = get_prime_inference_model_id(adapter=adapter)
    updated_run = record_prime_model_id_for_learn_run(run=run, prime_model_id=model_id)
    opencode = update_global_opencode_model(
        model_id=model_id,
        model_name=f"{session_status.session.session_name} #{updated_run.run_number}",
    )
    return LearnUseResult(
        session=session_status.session,
        run=updated_run,
        adapter=adapter,
        model_id=model_id,
        opencode=opencode,
    )
