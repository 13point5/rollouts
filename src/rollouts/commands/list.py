from __future__ import annotations

from rollouts.paths import get_app_paths
from rollouts.storage.db import SessionSummary, connect, initialize_db, list_sessions
from rollouts.storage.db import list_workspaces as db_list_workspaces


def list_all_sessions() -> list[SessionSummary]:
    paths = get_app_paths()
    with connect(paths) as connection:
        initialize_db(connection)
        return list_sessions(connection)


def list_all_workspaces() -> list:
    paths = get_app_paths()
    with connect(paths) as connection:
        initialize_db(connection)
        return db_list_workspaces(connection)
