import asyncio
import os
from collections.abc import Mapping
from pathlib import Path, PurePath
from typing import Any, Protocol
from uuid import UUID

from astra_domain import ApprovalState, Capability, CapabilityKind
from astra_model_providers import ToolDefinition
from astra_policy import evaluate_capability
from pydantic import BaseModel, ConfigDict, Field

from astra_agent.settings import Settings, TenantWorkspace

FILE_READ_CAPABILITY = Capability(kind=CapabilityKind.FILE_READ, scope="workspace")
COMMAND_EXECUTE_CAPABILITY = Capability(kind=CapabilityKind.COMMAND_EXECUTE, scope="workspace")


class ToolResult(BaseModel):
    """Result returned to the caller, which remains responsible for audit persistence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    success: bool
    content: str = ""
    error: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class SandboxRunner(Protocol):
    async def run(self, workspace: Path, argv: tuple[str, ...]) -> ToolResult: ...


class LocalTool(Protocol):
    definition: ToolDefinition
    capability: Capability

    async def execute(
        self, params: Mapping[str, Any], workspace: TenantWorkspace
    ) -> ToolResult: ...


def _relative_workspace_path(root: Path, value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty workspace-relative string")
    supplied = PurePath(value)
    if supplied.is_absolute() or ".." in supplied.parts:
        raise ValueError("path must be workspace-relative and may not contain '..'")
    candidate = root.joinpath(*supplied.parts)
    current = root
    for component in supplied.parts:
        current = current / component
        if current.is_symlink():
            raise ValueError("symbolic links are not allowed in workspace paths")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        raise ValueError("path escapes workspace")
    return resolved


class ReadFileTool:
    capability = FILE_READ_CAPABILITY
    definition = ToolDefinition(
        name="read_file",
        description="Read a workspace-relative text file, with a configured size limit.",
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        },
    )

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes

    async def execute(self, params: Mapping[str, Any], workspace: TenantWorkspace) -> ToolResult:
        try:
            path = _relative_workspace_path(workspace.root, params.get("path"))
            if not path.is_file():
                return ToolResult(success=False, error="path is not a regular file")
            with path.open("rb") as file:
                content = file.read(self._max_bytes + 1)
        except (OSError, ValueError) as error:
            return ToolResult(success=False, error=str(error))
        truncated = len(content) > self._max_bytes
        return ToolResult(
            success=True,
            content=content[: self._max_bytes].decode("utf-8", errors="replace"),
            data={"path": str(path.relative_to(workspace.root)), "truncated": truncated},
        )


class SearchFilesTool:
    capability = FILE_READ_CAPABILITY
    definition = ToolDefinition(
        name="search_files",
        description=(
            "Literal, case-insensitive search of text files under a workspace-relative path."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 1},
                "path": {"type": "string", "default": "."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    )

    def __init__(
        self, max_file_bytes: int, max_files: int, max_matches: int, max_output_bytes: int
    ) -> None:
        self._max_file_bytes = max_file_bytes
        self._max_files = max_files
        self._max_matches = max_matches
        self._max_output_bytes = max_output_bytes

    async def execute(self, params: Mapping[str, Any], workspace: TenantWorkspace) -> ToolResult:
        query = params.get("query")
        if not isinstance(query, str) or not query:
            return ToolResult(success=False, error="query must be a non-empty string")
        try:
            start = _relative_workspace_path(workspace.root, params.get("path", "."))
            if not start.is_dir():
                return ToolResult(success=False, error="path is not a directory")
        except (OSError, ValueError) as error:
            return ToolResult(success=False, error=str(error))

        matches: list[str] = []
        scanned_files = 0
        query_folded = query.casefold()
        for directory, directories, filenames in os.walk(start, followlinks=False):
            directory_path = Path(directory)
            directories[:] = [
                name for name in directories if not (directory_path / name).is_symlink()
            ]
            for filename in filenames:
                if scanned_files >= self._max_files or len(matches) >= self._max_matches:
                    break
                try:
                    relative = str((directory_path / filename).relative_to(workspace.root))
                    path = _relative_workspace_path(workspace.root, relative)
                    if not path.is_file():
                        continue
                    with path.open("rb") as file:
                        raw = file.read(self._max_file_bytes + 1)
                except (OSError, ValueError):
                    continue
                scanned_files += 1
                text = raw[: self._max_file_bytes].decode("utf-8", errors="replace")
                for line_number, line in enumerate(text.splitlines(), 1):
                    if query_folded not in line.casefold():
                        continue
                    match = f"{path.relative_to(workspace.root)}:{line_number}:{line}"
                    if sum(len(item) + 1 for item in matches) + len(match) > self._max_output_bytes:
                        return ToolResult(
                            success=True,
                            content="\n".join(matches),
                            data={"truncated": True, "scanned_files": scanned_files},
                        )
                    matches.append(match)
                    if len(matches) >= self._max_matches:
                        break
            if scanned_files >= self._max_files or len(matches) >= self._max_matches:
                break
        return ToolResult(
            success=True,
            content="\n".join(matches),
            data={
                "truncated": scanned_files >= self._max_files or len(matches) >= self._max_matches,
                "scanned_files": scanned_files,
            },
        )


class DockerPodmanSandboxRunner:
    """Container runner; the configured engine must itself be installed rootless."""

    def __init__(
        self,
        executable: Path,
        image: str,
        timeout_seconds: int,
        max_output_bytes: int,
        memory_limit: str,
        cpu_limit: float,
        pids_limit: int,
    ) -> None:
        self._executable = executable
        self._image = image
        self._timeout_seconds = timeout_seconds
        self._max_output_bytes = max_output_bytes
        self._memory_limit = memory_limit
        self._cpu_limit = cpu_limit
        self._pids_limit = pids_limit

    async def run(self, workspace: Path, argv: tuple[str, ...]) -> ToolResult:
        command = [
            str(self._executable),
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self._pids_limit),
            "--memory",
            self._memory_limit,
            "--cpus",
            str(self._cpu_limit),
            "--volume",
            f"{workspace}:/workspace:ro",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "PATH=/usr/bin:/bin",
            "--env",
            "LANG=C",
            "--env",
            "LC_ALL=C",
            self._image,
            *argv,
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={"PATH": "/usr/bin:/bin", "HOME": "/tmp", "LANG": "C", "LC_ALL": "C"},
        )
        assert process.stdout is not None
        assert process.stderr is not None
        try:
            waited, stdout, stderr = await asyncio.wait_for(
                asyncio.gather(
                    process.wait(),
                    self._read_bounded(process.stdout),
                    self._read_bounded(process.stderr),
                ),
                timeout=self._timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return ToolResult(success=False, error="sandbox command timed out")
        succeeded = waited == 0
        return ToolResult(
            success=succeeded,
            content=stdout[0].decode("utf-8", errors="replace"),
            error=None if succeeded else stderr[0].decode("utf-8", errors="replace"),
            data={
                "exit_code": waited,
                "output_truncated": stdout[1] or stderr[1],
            },
        )

    async def _read_bounded(self, stream: asyncio.StreamReader) -> tuple[bytes, bool]:
        chunks: list[bytes] = []
        total = 0
        truncated = False
        while chunk := await stream.read(8_192):
            remaining = self._max_output_bytes - total
            if remaining > 0:
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True
        return b"".join(chunks), truncated


class RunCommandTool:
    capability = COMMAND_EXECUTE_CAPABILITY
    definition = ToolDefinition(
        name="run_command",
        description="Run an argv command in the configured isolated workspace sandbox.",
        parameters={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                }
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
    )

    def __init__(self, runner: SandboxRunner) -> None:
        self._runner = runner

    async def execute(self, params: Mapping[str, Any], workspace: TenantWorkspace) -> ToolResult:
        argv = params.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(arg, str) and arg for arg in argv)
        ):
            return ToolResult(
                success=False, error="argv must be a non-empty array of non-empty strings"
            )
        return await self._runner.run(workspace.root, tuple(argv))


class LocalToolRegistry:
    """Dispatches configured local tools without persisting audit events."""

    def __init__(self, settings: Settings, runner: SandboxRunner | None = None) -> None:
        self._workspaces = settings.tenant_workspaces
        tools: dict[str, LocalTool] = {
            "read_file": ReadFileTool(settings.tool_read_max_bytes),
            "search_files": SearchFilesTool(
                settings.tool_search_max_file_bytes,
                settings.tool_search_max_files,
                settings.tool_search_max_matches,
                settings.tool_search_max_output_bytes,
            ),
        }
        configured_runner = runner
        if (
            configured_runner is None
            and settings.sandbox_executable is not None
            and settings.sandbox_image is not None
        ):
            configured_runner = DockerPodmanSandboxRunner(
                settings.sandbox_executable,
                settings.sandbox_image,
                settings.sandbox_timeout_seconds,
                settings.sandbox_max_output_bytes,
                settings.sandbox_memory_limit,
                settings.sandbox_cpu_limit,
                settings.sandbox_pids_limit,
            )
        if configured_runner is not None:
            tools["run_command"] = RunCommandTool(configured_runner)
        self._tools = tools

    def definitions(self, tenant_id: UUID) -> tuple[ToolDefinition, ...]:
        workspace = self._workspaces.get(tenant_id)
        if workspace is None:
            return ()
        return tuple(
            tool.definition
            for tool in self._tools.values()
            if evaluate_capability(tool.capability, workspace.grants).allowed
            or (
                tool.capability.kind is CapabilityKind.COMMAND_EXECUTE
                and evaluate_capability(
                    tool.capability, workspace.grants, ApprovalState.GRANTED
                ).allowed
            )
        )

    async def dispatch(
        self,
        tenant_id: UUID,
        name: str,
        params: Mapping[str, Any],
        approval_state: ApprovalState | None = None,
    ) -> ToolResult:
        workspace = self._workspaces.get(tenant_id)
        tool = self._tools.get(name)
        if workspace is None or tool is None:
            return ToolResult(success=False, error="local tool is unavailable")
        decision = evaluate_capability(tool.capability, workspace.grants, approval_state)
        if not decision.allowed:
            return ToolResult(
                success=False,
                error=decision.reason,
                data={"requires_approval": decision.requires_approval},
            )
        return await tool.execute(params, workspace)
