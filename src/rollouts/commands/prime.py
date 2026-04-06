from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic, sleep
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from rollouts.errors import RolloutsError


@dataclass(frozen=True)
class PrimeRunStartResult:
    run_id: str
    dashboard_url: str


@dataclass(frozen=True)
class PrimeRunStatusResult:
    run_id: str
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    dashboard_url: str


@dataclass(frozen=True)
class PrimeRunCheckpointResult:
    checkpoint_id: str
    run_id: str
    step: int
    status: str
    created_at: datetime
    uploaded_at: datetime | None


@dataclass(frozen=True)
class PrimeAdapterResult:
    adapter_id: str
    display_name: str | None
    run_id: str
    base_model: str
    step: int | None
    status: str
    deployment_status: str
    deployed_at: datetime | None
    deployment_error: str | None
    created_at: datetime
    updated_at: datetime


def get_prime_rl_run_logs(*, run_id: str, tail_lines: int = 1000) -> list[str]:
    try:
        from prime_cli.api.rl import RLClient
        from prime_cli.client import APIClient, APIError
        from prime_cli.commands.rl import clean_logs
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        rl_client = RLClient(api_client)
        raw_logs = rl_client.get_logs(run_id, tail_lines=tail_lines)
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return clean_logs(raw_logs)


def start_prime_rl_run(*, prime_config: str, config_path: Path) -> PrimeRunStartResult:
    try:
        from prime_cli.api.rl import RLClient
        from prime_cli.client import APIClient, APIError
        from prime_cli.core import Config as PrimeConfig
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    config = _load_prime_config(prime_config=prime_config)
    secrets = _collect_prime_secrets(config=config, config_path=config_path)
    _validate_wandb_config(config=config, secrets=secrets)

    try:
        api_client = APIClient()
        rl_client = RLClient(api_client)
        prime_app_config = PrimeConfig()
        run = rl_client.create_run(
            model_name=config.model,
            environments=[environment.to_api_dict() for environment in config.env],
            rollouts_per_example=config.rollouts_per_example,
            max_steps=config.max_steps,
            max_tokens=config.sampling.max_tokens,
            temperature=config.sampling.temperature,
            repetition_penalty=config.sampling.repetition_penalty,
            min_tokens=config.sampling.min_tokens,
            seed=config.sampling.seed,
            temp_scheduler=config.sampling.temp_scheduler.model_dump(exclude_none=True)
            if config.sampling.temp_scheduler is not None
            else None,
            extra_body=config.sampling.extra_body,
            batch_size=config.batch_size,
            name=config.name,
            wandb_entity=config.wandb.entity,
            wandb_project=config.wandb.project,
            wandb_run_name=config.wandb.name,
            secrets=secrets if secrets else None,
            team_id=prime_app_config.team_id,
            eval_config=config.eval.to_api_dict(),
            val_config=config.val.to_api_dict(),
            buffer_config=config.buffer.to_api_dict(),
            learning_rate=config.learning_rate,
            lora_alpha=config.lora_alpha,
            oversampling_factor=config.oversampling_factor,
            max_async_level=config.max_async_level,
            checkpoints_config=config.checkpoints.to_api_dict(),
            adapters_config=config.adapters.to_api_dict(),
            checkpoint_id=config.checkpoint_id,
            cluster_name=config.cluster_name,
            infrastructure_config=config.infrastructure.to_api_dict(),
        )
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return PrimeRunStartResult(
        run_id=run.id,
        dashboard_url=_dashboard_url(frontend_url=prime_app_config.frontend_url, run_id=run.id),
    )


def get_prime_rl_run_status(*, run_id: str) -> PrimeRunStatusResult:
    try:
        from prime_cli.api.rl import RLClient
        from prime_cli.client import APIClient, APIError
        from prime_cli.core import Config as PrimeConfig
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        rl_client = RLClient(api_client)
        prime_app_config = PrimeConfig()
        run = rl_client.get_run(run_id)
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return PrimeRunStatusResult(
        run_id=run.id,
        status=run.status,
        error_message=run.error_message,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        completed_at=run.completed_at,
        dashboard_url=_dashboard_url(frontend_url=prime_app_config.frontend_url, run_id=run.id),
    )


def list_prime_rl_run_checkpoints(*, run_id: str) -> list[PrimeRunCheckpointResult]:
    try:
        from prime_cli.api.rl import RLClient
        from prime_cli.client import APIClient, APIError
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        rl_client = RLClient(api_client)
        checkpoints = rl_client.list_checkpoints(run_id)
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return [
        PrimeRunCheckpointResult(
            checkpoint_id=checkpoint.id,
            run_id=checkpoint.rft_run_id,
            step=checkpoint.step,
            status=checkpoint.status,
            created_at=checkpoint.created_at,
            uploaded_at=checkpoint.uploaded_at,
        )
        for checkpoint in checkpoints
    ]


def get_latest_prime_rl_run_checkpoint(*, run_id: str) -> PrimeRunCheckpointResult | None:
    checkpoints = list_prime_rl_run_checkpoints(run_id=run_id)
    uploaded_checkpoints = [checkpoint for checkpoint in checkpoints if checkpoint.uploaded_at]
    if not uploaded_checkpoints:
        return None

    return max(
        uploaded_checkpoints,
        key=lambda checkpoint: (
            checkpoint.step,
            checkpoint.uploaded_at or checkpoint.created_at,
        ),
    )


def list_prime_run_adapters(*, run_id: str) -> list[PrimeAdapterResult]:
    adapters = _list_prime_adapters()
    return [adapter for adapter in adapters if adapter.run_id == run_id]


def get_prime_adapter(*, adapter_id: str) -> PrimeAdapterResult:
    try:
        from prime_cli.api.deployments import DeploymentsClient
        from prime_cli.client import APIClient, APIError
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        deployments_client = DeploymentsClient(api_client)
        adapter = deployments_client.get_adapter(adapter_id)
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return _adapter_result(adapter=adapter)


def get_prime_inference_model_id(*, adapter: PrimeAdapterResult) -> str:
    return f"{adapter.base_model}:{adapter.adapter_id}"


def ensure_prime_run_adapter_deployed(
    *,
    run_id: str,
    timeout_seconds: float = 900.0,
    poll_interval_seconds: float = 5.0,
) -> PrimeAdapterResult:
    adapters = list_prime_run_adapters(run_id=run_id)
    selected_adapter = _select_preferred_prime_adapter(run_id=run_id, adapters=adapters)
    selected_adapter = _wait_for_prime_adapter_ready(
        adapter=selected_adapter,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    if selected_adapter.deployment_status == "DEPLOYED":
        return selected_adapter
    if selected_adapter.deployment_status == "DEPLOYING":
        return _wait_for_prime_adapter_deployment(
            adapter_id=selected_adapter.adapter_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    if selected_adapter.deployment_status == "UNLOADING":
        selected_adapter = _wait_for_prime_adapter_deployment_state(
            adapter_id=selected_adapter.adapter_id,
            terminal_states={"NOT_DEPLOYED", "UNLOAD_FAILED"},
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )

    if selected_adapter.status != "READY":
        raise RolloutsError(
            f"Prime adapter is not ready for deployment; status is {selected_adapter.status!r}"
        )

    deployable_models = _get_prime_deployable_models()
    if selected_adapter.base_model not in deployable_models:
        raise RolloutsError(
            f"Prime adapter base model is not currently deployable: {selected_adapter.base_model}"
        )

    _deploy_prime_adapter(adapter_id=selected_adapter.adapter_id)
    return _wait_for_prime_adapter_deployment(
        adapter_id=selected_adapter.adapter_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _load_prime_config(*, prime_config: str) -> Any:
    try:
        from prime_cli.commands.rl import RLConfig
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        config_data = tomllib.loads(prime_config)
    except tomllib.TOMLDecodeError as error:
        raise RolloutsError(f"invalid Prime config TOML: {error}") from error

    try:
        return RLConfig.model_validate(config_data)
    except PydanticValidationError as error:
        formatted_errors = "; ".join(_format_validation_errors(error))
        raise RolloutsError(f"invalid Prime config: {formatted_errors}") from error


def _format_validation_errors(error: PydanticValidationError) -> list[str]:
    messages: list[str] = []
    for entry in error.errors():
        path = ".".join(str(part) for part in entry["loc"])
        message = str(entry["msg"])
        if message.startswith("Value error, "):
            message = message[len("Value error, ") :]
        messages.append(f"{path}: {message}" if path else message)
    return messages


def _collect_prime_secrets(*, config: Any, config_path: Path) -> dict[str, str]:
    try:
        from prime_cli.utils.env_vars import EnvParseError, collect_env_vars
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    config_dir = config_path.parent
    config_env_files = config.env_file + config.env_files
    resolved_env_files = [
        str((config_dir / env_file).resolve(strict=False)) for env_file in config_env_files
    ]

    try:
        return collect_env_vars(
            env_files=resolved_env_files if resolved_env_files else None,
        )
    except EnvParseError as error:
        raise RolloutsError(str(error)) from error


def _validate_wandb_config(*, config: Any, secrets: dict[str, str]) -> None:
    wandb_configured = config.wandb.entity or config.wandb.project
    if wandb_configured and "WANDB_API_KEY" not in secrets:
        raise RolloutsError(
            "WANDB_API_KEY is required when W&B monitoring is configured in the Prime config"
        )


def _dashboard_url(*, frontend_url: str, run_id: str) -> str:
    return f"{frontend_url}/dashboard/training/{run_id}"


def _list_prime_adapters() -> list[PrimeAdapterResult]:
    try:
        from prime_cli.api.deployments import DeploymentsClient
        from prime_cli.client import APIClient, APIError
        from prime_cli.core import Config as PrimeConfig
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        deployments_client = DeploymentsClient(api_client)
        prime_app_config = PrimeConfig()
        adapters: list[Any] = []
        total = 0
        offset = 0
        page_size = 100

        while offset == 0 or offset < total:
            page_adapters, total = deployments_client.list_adapters(
                team_id=prime_app_config.team_id,
                limit=page_size,
                offset=offset,
            )
            if not page_adapters:
                break
            adapters.extend(page_adapters)
            offset += len(page_adapters)
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return [_adapter_result(adapter=adapter) for adapter in adapters]


def _adapter_result(*, adapter: Any) -> PrimeAdapterResult:
    return PrimeAdapterResult(
        adapter_id=adapter.id,
        display_name=adapter.display_name,
        run_id=adapter.rft_run_id,
        base_model=adapter.base_model,
        step=adapter.step,
        status=adapter.status,
        deployment_status=adapter.deployment_status,
        deployed_at=adapter.deployed_at,
        deployment_error=adapter.deployment_error,
        created_at=adapter.created_at,
        updated_at=adapter.updated_at,
    )


def _select_preferred_prime_adapter(
    *,
    run_id: str,
    adapters: list[PrimeAdapterResult],
) -> PrimeAdapterResult:
    if not adapters:
        raise RolloutsError(f"Prime run {run_id!r} does not have any adapters yet")

    non_failed_adapters = [adapter for adapter in adapters if adapter.status != "FAILED"]
    if not non_failed_adapters:
        raise RolloutsError(f"Prime run {run_id!r} only has failed adapters")

    return max(non_failed_adapters, key=_prime_adapter_sort_key)


def _prime_adapter_sort_key(adapter: PrimeAdapterResult) -> tuple[int, datetime, datetime]:
    return (
        adapter.step if adapter.step is not None else -1,
        adapter.updated_at,
        adapter.created_at,
    )


def _wait_for_prime_adapter_ready(
    *,
    adapter: PrimeAdapterResult,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> PrimeAdapterResult:
    if adapter.status == "READY":
        return adapter
    if adapter.status not in {"PENDING", "UPLOADING"}:
        return adapter

    return _wait_for_prime_adapter_status(
        adapter_id=adapter.adapter_id,
        terminal_states={"READY", "FAILED"},
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _wait_for_prime_adapter_deployment(
    *,
    adapter_id: str,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> PrimeAdapterResult:
    return _wait_for_prime_adapter_deployment_state(
        adapter_id=adapter_id,
        terminal_states={"DEPLOYED", "DEPLOY_FAILED"},
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )


def _wait_for_prime_adapter_status(
    *,
    adapter_id: str,
    terminal_states: set[str],
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> PrimeAdapterResult:
    deadline = monotonic() + timeout_seconds
    while True:
        adapter = get_prime_adapter(adapter_id=adapter_id)
        if adapter.status in terminal_states:
            if adapter.status == "FAILED":
                raise RolloutsError(f"Prime adapter failed: {adapter.adapter_id}")
            return adapter
        if monotonic() >= deadline:
            raise RolloutsError(
                "timed out waiting for Prime adapter status; "
                f"adapter={adapter.adapter_id} status={adapter.status}"
            )
        sleep(poll_interval_seconds)


def _wait_for_prime_adapter_deployment_state(
    *,
    adapter_id: str,
    terminal_states: set[str],
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> PrimeAdapterResult:
    deadline = monotonic() + timeout_seconds
    while True:
        adapter = get_prime_adapter(adapter_id=adapter_id)
        if adapter.deployment_status in terminal_states:
            if adapter.deployment_status == "DEPLOY_FAILED":
                message = adapter.deployment_error or adapter.deployment_status
                raise RolloutsError(f"Prime adapter deployment failed: {message}")
            return adapter
        if monotonic() >= deadline:
            raise RolloutsError(
                "timed out waiting for Prime adapter deployment; "
                f"adapter={adapter.adapter_id} deployment_status={adapter.deployment_status}"
            )
        sleep(poll_interval_seconds)


def _deploy_prime_adapter(*, adapter_id: str) -> PrimeAdapterResult:
    try:
        from prime_cli.api.deployments import DeploymentsClient
        from prime_cli.client import APIClient, APIError
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        deployments_client = DeploymentsClient(api_client)
        adapter = deployments_client.deploy_adapter(adapter_id)
    except APIError as error:
        raise RolloutsError(str(error)) from error

    return _adapter_result(adapter=adapter)


def _get_prime_deployable_models() -> set[str]:
    try:
        from prime_cli.api.deployments import DeploymentsClient
        from prime_cli.client import APIClient, APIError
    except ModuleNotFoundError as error:
        raise RolloutsError(
            "Prime SDK is not installed in the current Python environment"
        ) from error

    try:
        api_client = APIClient()
        deployments_client = DeploymentsClient(api_client)
        return set(deployments_client.get_deployable_models())
    except APIError as error:
        raise RolloutsError(str(error)) from error
