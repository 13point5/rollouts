from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from rollouts.errors import RolloutsError

PLUGIN_FILENAME = "rollouts.ts"
PLUGIN_SOURCE = """import { execFile } from "node:child_process";
import { promisify } from "node:util";

const execFileAsync = promisify(execFile);

function isCompletedAssistantMessageEvent(event) {
  if (event.type !== "message.updated") {
    return false;
  }

  const { info } = event.properties;

  return (
    info.role === "assistant" &&
    info.finish === "stop" &&
    info.time.completed !== undefined
  );
}

export const RolloutsPlugin = async (input, rawOptions) => {
  void rawOptions;

  async function runSnapshot(name, sessionID, messageID, payload) {
    const metadata = {
      timestamp: new Date().toISOString(),
      kind: name === "chat.message" ? "hooks" : "events",
      name,
      sessionID,
      messageID,
      context: {
        directory: input.directory,
        worktree: input.worktree,
        serverUrl: input.serverUrl.toString(),
        projectID: input.project.id,
      },
      payload,
    };

    const args = [
      "snapshot",
      ".",
      "--session",
      sessionID,
      "--message",
      messageID,
      "--metadata",
      JSON.stringify(metadata),
    ];

    try {
      await execFileAsync("rollouts", args, {
        cwd: input.directory,
      });
    } catch (error) {
      console.error("[opencode-rollouts] failed to run rollouts snapshot");
      console.error(error);
    }
  }

  return {
    event: async ({ event }) => {
      if (!isCompletedAssistantMessageEvent(event)) {
        return;
      }

      await runSnapshot(
        "assistant.message.completed",
        event.properties.info.sessionID,
        event.properties.info.id,
        { event },
      );
    },

    "chat.message": async (hookInput, hookOutput) => {
      await runSnapshot(
        "chat.message",
        hookInput.sessionID,
        hookOutput.message.id,
        {
          input: hookInput,
          output: hookOutput,
        },
      );
    },
  };
};

export default RolloutsPlugin;
"""


@dataclass(frozen=True)
class SetupResult:
    scope: str
    plugin_path: Path
    replaced_existing: bool


def validate_setup_scope(scope: str) -> str:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"global", "project"}:
        raise RolloutsError("--scope must be either 'global' or 'project'")
    return normalized_scope


def ensure_opencode_installed() -> None:
    if shutil.which("opencode") is None:
        raise RolloutsError("opencode executable not found; install OpenCode first")


def install_opencode_plugin(*, scope: str, workspace: Path) -> SetupResult:
    ensure_opencode_installed()

    normalized_scope = validate_setup_scope(scope)
    plugins_dir = (
        _global_opencode_plugins_dir()
        if normalized_scope == "global"
        else workspace.resolve(strict=False) / ".opencode" / "plugins"
    )
    plugin_path = plugins_dir / PLUGIN_FILENAME
    replaced_existing = plugin_path.exists()

    plugins_dir.mkdir(parents=True, exist_ok=True)
    plugin_path.write_text(f"{PLUGIN_SOURCE}\n", encoding="utf-8")

    return SetupResult(
        scope=normalized_scope,
        plugin_path=plugin_path,
        replaced_existing=replaced_existing,
    )


def _global_opencode_plugins_dir() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if config_home:
        return Path(config_home).expanduser().resolve(strict=False) / "opencode" / "plugins"
    return Path("~/.config/opencode/plugins").expanduser().resolve(strict=False)
