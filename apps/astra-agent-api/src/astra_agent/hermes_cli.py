import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import UUID

from astra_runtime import MariaDBRuntimeStore
from sqlalchemy.ext.asyncio import create_async_engine

from astra_agent.hermes import dry_run, dry_run_profiles, stage_profile_records, stage_records
from astra_agent.settings import Settings


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only local Hermes migration importer")
    parser.add_argument("--tenant", type=UUID, required=True)
    parser.add_argument("--database", type=Path, help="Hermes Holographic SQLite database")
    parser.add_argument("--soul", type=Path, help="Hermes SOUL.md (inventory only)")
    parser.add_argument("--user", type=Path, help="Hermes USER.md")
    parser.add_argument("--memory", type=Path, help="Hermes MEMORY.md")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--stage", action="store_true")
    return parser


def _soul_inventory(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError("Hermes SOUL.md path is not a readable file")
    raw = path.read_bytes()
    raw.decode("utf-8")
    return {
        "filename": path.name,
        "bytes": len(raw),
        "fingerprint": hashlib.sha256(raw).hexdigest(),
        "status": "manual_persona_review_required",
        "required_authored_core_fields": [
            "identity",
            "values",
            "boundaries",
            "tone",
            "initiative",
            "emotional_range",
            "disagreement",
        ],
    }


async def _stage(args: argparse.Namespace) -> list[dict[str, Any]]:
    settings = Settings()
    if not settings.database_url:
        raise ValueError("--stage requires ASTRA_DATABASE_URL")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    store = MariaDBRuntimeStore(engine)
    staged: list[dict[str, Any]] = []
    try:
        if args.database:
            batch, records = stage_records(args.database, args.tenant)
            stored = await store.stage_migration_batch(batch, records, args.tenant)
            staged.append({"batch_id": stored.id, "source_system": stored.source_system})
        if args.user or args.memory:
            batch, records = stage_profile_records(args.tenant, args.user, args.memory)
            stored = await store.stage_migration_batch(batch, records, args.tenant)
            staged.append({"batch_id": stored.id, "source_system": stored.source_system})
    finally:
        await engine.dispose()
    return staged


def main() -> None:
    args = _parser().parse_args()
    if not any((args.database, args.soul, args.user, args.memory)):
        raise SystemExit("at least one source path is required")
    result: dict[str, Any] = {}
    if args.database:
        result["holographic"] = asdict(dry_run(args.database))
    if args.user or args.memory:
        result["profile_files"] = asdict(dry_run_profiles(args.user, args.memory))
    if args.soul:
        result["soul"] = _soul_inventory(args.soul)
    if args.stage:
        if not (args.database or args.user or args.memory):
            raise SystemExit("SOUL.md is inventory-only; no stageable source was selected")
        result["staged"] = asyncio.run(_stage(args))
    print(json.dumps(result, indent=2, default=str, sort_keys=True))


if __name__ == "__main__":
    main()
