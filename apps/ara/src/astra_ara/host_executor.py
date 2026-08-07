import json
import subprocess
from pathlib import Path

from astra_domain import Task

from astra_ara.settings import HostARASettings


class HostCommandExecutor:
    """Executes explicitly delegated argv commands under the host ARA's local policy."""

    def __init__(self, settings: HostARASettings) -> None:
        self._settings = settings

    def execute(self, task: Task) -> str:
        try:
            payload = json.loads(task.context)
        except json.JSONDecodeError as error:
            raise ValueError("host command task has invalid payload") from error
        argv = payload.get("argv")
        cwd = payload.get("cwd")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) and item for item in argv)
            or (cwd is not None and not isinstance(cwd, str))
        ):
            raise ValueError("host command task has invalid argv or cwd")
        executable = Path(argv[0]).expanduser().resolve(strict=True)
        working_directory = Path(cwd).expanduser().resolve(strict=True) if cwd else None
        self._authorize(executable, working_directory)
        try:
            completed = subprocess.run(
                [str(executable), *argv[1:]],
                cwd=working_directory,
                capture_output=True,
                text=True,
                timeout=self._settings.command_timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("host command timed out") from error
        raw_output = (completed.stdout + completed.stderr).encode()
        output = raw_output[: self._settings.command_max_output_bytes]
        return json.dumps(
            {
                "argv": argv,
                "cwd": str(working_directory) if working_directory else None,
                "exit_code": completed.returncode,
                "output": output.decode(errors="replace"),
                "output_truncated": len(output)
                < len(raw_output),
            },
            ensure_ascii=True,
        )

    def _authorize(self, executable: Path, cwd: Path | None) -> None:
        if self._settings.execution_mode == "unrestricted_autonomous":
            return
        if self._settings.execution_mode == "approval_required":
            raise PermissionError("host ARA is configured to require approval for every command")
        allowed_commands = {
            path.expanduser().resolve(strict=True) for path in self._settings.command_allowlist
        }
        if executable not in allowed_commands:
            raise PermissionError("command is not in the host ARA allowlist")
        if self._settings.working_directory_allowlist and cwd is not None:
            roots = tuple(
                path.expanduser().resolve(strict=True)
                for path in self._settings.working_directory_allowlist
            )
            if not any(cwd.is_relative_to(root) for root in roots):
                raise PermissionError("working directory is not in the host ARA allowlist")
