from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
