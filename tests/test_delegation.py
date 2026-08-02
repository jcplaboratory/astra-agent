from pathlib import Path
from uuid import uuid4

from astra_ara.executor import ARARepositoryExecutor


def test_repository_executor_never_leaves_configured_root(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("password secret", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    (root / "README.md").write_text("authentication overview", encoding="utf-8")
    from astra_domain import Task

    result = ARARepositoryExecutor(root).execute(
        Task(
            tenant_id=uuid4(),
            objective="Inspect repository authentication",
            deliverable_contract="report",
        )
    )
    assert "README.md" in result
    assert "link.txt" not in result
    assert "password secret" not in result
