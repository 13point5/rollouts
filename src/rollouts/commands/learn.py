from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import tomlkit
from tomlkit.exceptions import TOMLKitError

from rollouts.commands.hf import resolve_dataset_repo_id as resolve_hf_dataset_repo_id
from rollouts.errors import RolloutsError
from rollouts.models import LearnRunRecord, LearnSessionRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.storage.db import (
    connect,
    get_learn_session,
    initialize_db,
    save_learn_run,
    save_learn_session,
)
from rollouts.storage.db import (
    delete_learn_session as db_delete_learn_session,
)
from rollouts.storage.db import (
    get_learn_run_by_number as db_get_learn_run_by_number,
)
from rollouts.storage.db import (
    list_learn_runs as db_list_learn_runs,
)
from rollouts.storage.db import (
    list_learn_sessions as db_list_learn_sessions,
)

DEFAULT_DATASET_SUFFIX = "-rollouts-learn"


@dataclass(frozen=True)
class LearnSessionStatus:
    session: LearnSessionRecord
    run_count: int
    latest_run: LearnRunRecord | None


def suggest_dataset_repo_name(*, session_name: str) -> str:
    normalized_name = session_name.strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized_name).strip("-")
    if not slug:
        raise RolloutsError("session name does not contain any valid dataset name characters")
    return f"{slug}{DEFAULT_DATASET_SUFFIX}"


def normalize_dataset_repo(*, dataset_repo: str) -> str:
    normalized_dataset_repo = dataset_repo.strip()
    if not normalized_dataset_repo:
        raise RolloutsError("dataset repo cannot be empty")

    parsed = urlparse(normalized_dataset_repo)
    if parsed.scheme or parsed.netloc:
        if parsed.netloc not in {"huggingface.co", "www.huggingface.co"}:
            raise RolloutsError("dataset URL must be on huggingface.co")

        path_parts = [part for part in parsed.path.split("/") if part]
        if len(path_parts) < 3 or path_parts[0] != "datasets":
            raise RolloutsError(
                "dataset URL must look like https://huggingface.co/datasets/<owner>/<name>"
            )
        return "/".join(path_parts[1:3])

    return normalized_dataset_repo


def resolve_dataset_repo_id(*, dataset_repo: str) -> str:
    normalized_dataset_repo = normalize_dataset_repo(dataset_repo=dataset_repo)
    if "/" in normalized_dataset_repo:
        return normalized_dataset_repo

    return resolve_hf_dataset_repo_id(name=normalized_dataset_repo)


def suggest_dataset_repo_id(*, session_name: str) -> str:
    suggested_name = suggest_dataset_repo_name(session_name=session_name)
    return resolve_dataset_repo_id(dataset_repo=suggested_name)


def add_dataset_to_prime_config(*, prime_config: str, dataset_repo: str) -> str:
    try:
        document = tomlkit.parse(prime_config)
    except TOMLKitError as error:
        raise RolloutsError("config file is not valid TOML") from error

    envs = document.get("env")
    if not isinstance(envs, list) or not envs:
        raise RolloutsError("config file must include at least one [[env]] section")

    for env in envs:
        if not hasattr(env, "get") or not hasattr(env, "__setitem__"):
            raise RolloutsError("each [[env]] entry must be a TOML table")

        args = env.get("args")
        if args is None:
            args = tomlkit.inline_table()
            env["args"] = args
        elif not hasattr(args, "__setitem__"):
            raise RolloutsError("each [[env]] entry args value must be a TOML table")

        args["dataset"] = dataset_repo

    return tomlkit.dumps(document)


def create_learn_session(
    *,
    session_name: str,
    dataset_repo: str,
    config_path: Path,
) -> LearnSessionRecord:
    normalized_session_name = session_name.strip()
    if not normalized_session_name:
        raise RolloutsError("session name cannot be empty")

    normalized_dataset_repo = resolve_dataset_repo_id(dataset_repo=dataset_repo)

    try:
        prime_config = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RolloutsError(f"failed to read config file: {config_path}") from error

    if not prime_config.strip():
        raise RolloutsError("config file cannot be empty")

    prime_config = add_dataset_to_prime_config(
        prime_config=prime_config,
        dataset_repo=normalized_dataset_repo,
    )

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        existing = get_learn_session(
            connection,
            session_name=normalized_session_name,
        )
        if existing is not None:
            raise RolloutsError(f"learn session already exists: {normalized_session_name!r}")

        return save_learn_session(
            connection,
            session_name=normalized_session_name,
            dataset_repo=normalized_dataset_repo,
            prime_config=prime_config,
        )


def list_all_learn_sessions() -> list[LearnSessionRecord]:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return db_list_learn_sessions(connection)


def list_learn_session_statuses() -> list[LearnSessionStatus]:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        sessions = db_list_learn_sessions(connection)
        return [
            LearnSessionStatus(
                session=session,
                run_count=len(runs := db_list_learn_runs(connection, session_id=session.id)),
                latest_run=runs[-1] if runs else None,
            )
            for session in sessions
        ]


def get_learn_session_status(*, session_name: str) -> LearnSessionStatus:
    normalized_session_name = session_name.strip()
    if not normalized_session_name:
        raise RolloutsError("session name cannot be empty")

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        session = get_learn_session(
            connection,
            session_name=normalized_session_name,
        )
        if session is None:
            raise RolloutsError(f"learn session not found: {normalized_session_name!r}")

        runs = db_list_learn_runs(connection, session_id=session.id)
        return LearnSessionStatus(
            session=session,
            run_count=len(runs),
            latest_run=runs[-1] if runs else None,
        )


def get_learn_run_for_session(
    *,
    session_name: str,
    run_number: int | None = None,
) -> tuple[LearnSessionStatus, LearnRunRecord]:
    normalized_session_name = session_name.strip()
    if not normalized_session_name:
        raise RolloutsError("session name cannot be empty")

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        session = get_learn_session(
            connection,
            session_name=normalized_session_name,
        )
        if session is None:
            raise RolloutsError(f"learn session not found: {normalized_session_name!r}")

        runs = db_list_learn_runs(connection, session_id=session.id)
        session_status = LearnSessionStatus(
            session=session,
            run_count=len(runs),
            latest_run=runs[-1] if runs else None,
        )

        if run_number is None:
            if session_status.latest_run is None:
                raise RolloutsError(f"learn session has no runs: {session.session_name!r}")
            return session_status, session_status.latest_run

        run = db_get_learn_run_by_number(connection, session_id=session.id, run_number=run_number)
        if run is None:
            raise RolloutsError(
                f"learn session {session.session_name!r} does not have run #{run_number}"
            )
        return session_status, run


def delete_learn_session_by_name(*, session_name: str) -> int:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return db_delete_learn_session(connection, session_name=session_name)


def read_learn_run_config_file(
    *,
    config_path: Path,
    dataset_repo: str,
) -> str:
    try:
        prime_config = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RolloutsError(f"failed to read config file: {config_path}") from error

    if not prime_config.strip():
        raise RolloutsError("config file cannot be empty")

    return add_dataset_to_prime_config(
        prime_config=prime_config,
        dataset_repo=dataset_repo,
    )


def resolve_learn_run_config_inputs(
    *,
    session: LearnSessionRecord,
    source_run: LearnRunRecord,
    config_path: Path | None = None,
    action_label: str = "continue",
) -> tuple[str, Path]:
    if config_path is not None:
        resolved_config_path = config_path.resolve(strict=False)
        resolved_prime_config = read_learn_run_config_file(
            config_path=resolved_config_path,
            dataset_repo=session.dataset_repo,
        )
    else:
        if source_run.config_path is None:
            raise RolloutsError(
                f"source run does not have a stored config path; pass --config to {action_label} it"
            )

        resolved_config_path = source_run.config_path.expanduser().resolve(strict=False)
        if not resolved_config_path.exists():
            raise RolloutsError(
                "stored config path no longer exists: "
                f"{resolved_config_path}; pass --config to {action_label}"
            )
        if not resolved_config_path.is_file():
            raise RolloutsError(
                "stored config path is not a file: "
                f"{resolved_config_path}; pass --config to {action_label}"
            )

        resolved_prime_config = source_run.prime_config

    return resolved_prime_config, resolved_config_path


def set_prime_config_checkpoint(
    *,
    prime_config: str,
    checkpoint_id: str | None,
) -> str:
    try:
        document = tomlkit.parse(prime_config)
    except TOMLKitError as error:
        raise RolloutsError("config file is not valid TOML") from error

    if checkpoint_id is None:
        document.pop("checkpoint_id", None)
    else:
        document["checkpoint_id"] = checkpoint_id

    return tomlkit.dumps(document)


def create_initial_learn_run(
    *,
    session: LearnSessionRecord,
    config_path: Path,
) -> LearnRunRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return save_learn_run(
            connection,
            run_id=uuid4().hex,
            session_id=session.id,
            type="start",
            prime_config=session.prime_config,
            config_path=config_path.resolve(strict=False),
        )


def create_restarted_learn_run(
    *,
    session: LearnSessionRecord,
    source_run: LearnRunRecord,
    prime_config: str,
    config_path: Path,
    prime_run_id: str,
) -> LearnRunRecord:
    return _create_child_learn_run(
        session=session,
        source_run=source_run,
        type="restart",
        prime_config=prime_config,
        config_path=config_path,
        prime_run_id=prime_run_id,
    )


def create_continued_learn_run(
    *,
    session: LearnSessionRecord,
    source_run: LearnRunRecord,
    prime_config: str,
    config_path: Path,
    prime_run_id: str,
    source_checkpoint_id: str | None,
) -> LearnRunRecord:
    return _create_child_learn_run(
        session=session,
        source_run=source_run,
        type="continue",
        prime_config=prime_config,
        config_path=config_path,
        prime_run_id=prime_run_id,
        source_checkpoint_id=source_checkpoint_id,
    )


def _create_child_learn_run(
    *,
    session: LearnSessionRecord,
    source_run: LearnRunRecord,
    type: str,
    prime_config: str,
    config_path: Path,
    prime_run_id: str,
    source_checkpoint_id: str | None = None,
) -> LearnRunRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return save_learn_run(
            connection,
            run_id=uuid4().hex,
            session_id=session.id,
            type=type,
            prime_run_id=prime_run_id,
            source_checkpoint_id=source_checkpoint_id,
            prime_config=prime_config,
            config_path=config_path.resolve(strict=False),
            parent_run_id=source_run.id,
        )


def record_prime_run_id_for_learn_run(
    *,
    run: LearnRunRecord,
    prime_run_id: str,
) -> LearnRunRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return save_learn_run(
            connection,
            run_id=run.id,
            session_id=run.session_id,
            type=run.type,
            prime_run_id=prime_run_id,
            source_checkpoint_id=run.source_checkpoint_id,
            prime_checkpoint_id=run.prime_checkpoint_id,
            prime_model_id=run.prime_model_id,
            prime_config=run.prime_config,
            config_path=run.config_path,
            parent_run_id=run.parent_run_id,
        )
