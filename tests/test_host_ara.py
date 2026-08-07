import json
from pathlib import Path
from uuid import uuid4

import pytest
from astra_ara.host_executor import HostCommandExecutor
from astra_ara.settings import HostARASettings
from astra_domain import Task
from pydantic import ValidationError


def _settings(**updates: object) -> HostARASettings:
    values: dict[str, object] = {
        "tenant_id": uuid4(),
        "ara_id": uuid4(),
        "enabled": True,
        "execution_mode": "unrestricted_autonomous",
        "confirm_unrestricted_autonomy": True,
    }
    values.update(updates)
    return HostARASettings(**values)


def _task(argv: list[str], cwd: str | None = None) -> Task:
    return Task(
        tenant_id=uuid4(),
        objective="test command",
        context=json.dumps({"argv": argv, "cwd": cwd}),
        deliverable_contract="Return bounded host command exit status and output.",
    )


def test_host_ara_requires_explicit_unrestricted_confirmation() -> None:
    with pytest.raises(ValidationError, match="CONFIRM_UNRESTRICTED"):
        _settings(confirm_unrestricted_autonomy=False)


def test_host_executor_runs_direct_argv() -> None:
    result = json.loads(HostCommandExecutor(_settings()).execute(_task(["/usr/bin/true"])))

    assert result["exit_code"] == 0
    assert result["argv"] == ["/usr/bin/true"]


def test_host_executor_enforces_allowlist(tmp_path: Path) -> None:
    settings = _settings(
        execution_mode="autonomous_allowlist",
        command_allowlist=(Path("/usr/bin/true"),),
        working_directory_allowlist=(tmp_path,),
    )
    executor = HostCommandExecutor(settings)

    assert json.loads(executor.execute(_task(["/usr/bin/true"], str(tmp_path))))["exit_code"] == 0
    with pytest.raises(PermissionError, match="allowlist"):
        executor.execute(_task(["/usr/bin/false"], str(tmp_path)))
