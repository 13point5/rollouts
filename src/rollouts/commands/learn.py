from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

from rollouts.errors import RolloutsError
from rollouts.models import LearnSessionRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.storage.db import (
    connect,
    get_learn_session,
    initialize_db,
    save_learn_session,
)
from rollouts.storage.db import (
    list_learn_sessions as db_list_learn_sessions,
)

DEFAULT_DATASET_SUFFIX = "--rollouts-learn"


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


def create_learn_session(
    *,
    session_name: str,
    dataset_repo: str,
    config_path: Path,
) -> LearnSessionRecord:
    normalized_session_name = session_name.strip()
    if not normalized_session_name:
        raise RolloutsError("session name cannot be empty")

    normalized_dataset_repo = normalize_dataset_repo(dataset_repo=dataset_repo)

    try:
        prime_config = config_path.read_text(encoding="utf-8")
    except OSError as error:
        raise RolloutsError(f"failed to read config file: {config_path}") from error

    if not prime_config.strip():
        raise RolloutsError("config file cannot be empty")

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
