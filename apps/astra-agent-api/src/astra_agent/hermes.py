"""Strict, read-only adapter for the supported Hermes Holographic SQLite layout."""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import UUID, uuid4

from astra_domain import MemoryKind, MemoryRecord, MemoryState, MigrationBatch, PersonaCore


class HermesSchemaError(ValueError):
    pass


@dataclass(frozen=True)
class HermesDryRun:
    fingerprint: str
    fact_count: int
    entity_count: int
    trust_min: float | None
    trust_max: float | None
    candidate_count: int


@dataclass(frozen=True)
class HermesProfileDryRun:
    fingerprint: str
    user_count: int
    memory_count: int
    sensitive_count: int
    candidate_count: int


@dataclass(frozen=True)
class _ProfileItem:
    role: str
    filename: str
    heading: str | None
    line_number: int
    content: str


_TABLES = {
    "facts": {
        "fact_id",
        "content",
        "category",
        "tags",
        "trust_score",
        "retrieval_count",
        "helpful_count",
        "created_at",
        "updated_at",
        "hrr_vector",
    },
    "entities": {"entity_id", "name", "entity_type", "aliases", "created_at"},
    "fact_entities": {"fact_id", "entity_id"},
    "memory_banks": {"bank_id", "bank_name", "vector", "dim", "fact_count", "updated_at"},
}
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$")
_BULLET = re.compile(r"^\s*(?:[-+*]|\d+[.)])\s+(.+?)\s*$")
_SENSITIVE = re.compile(
    r"\b(?:security|secrets?|password|credentials?|tokens?|api[ -]?keys?|address|"
    r"private|privacy|intimate)\b",
    re.IGNORECASE,
)
_PROJECT_HEADING = re.compile(
    r"\b(?:projects?|work|repositories|repository|repos?)\b", re.IGNORECASE
)
_FACT_HEADING = re.compile(r"\b(?:facts?|about|profiles?|identity|bio)\b", re.IGNORECASE)
_PREFERENCE_HEADING = re.compile(
    r"\b(?:preferences?|prefer|styles?|communication|conventions?|privacy|boundaries|boundary)\b",
    re.IGNORECASE,
)


def _connect(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise HermesSchemaError("Hermes database path is not a readable file")
    # immutable prevents SQLite from taking locks or writing journals to the source.
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _verify(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    allowed = set(_TABLES) | {
        "sqlite_sequence",
        "facts_fts",
        "facts_fts_config",
        "facts_fts_data",
        "facts_fts_docsize",
        "facts_fts_idx",
    }
    if not set(_TABLES).issubset(tables) or not tables.issubset(allowed):
        raise HermesSchemaError("unsupported Hermes SQLite schema")
    for table, required in _TABLES.items():
        columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}
        if columns != required:
            raise HermesSchemaError("unsupported Hermes SQLite schema")


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_profile(path: Path, role: str) -> tuple[bytes, tuple[_ProfileItem, ...]]:
    if not path.is_file():
        raise HermesSchemaError(f"Hermes {role} path is not a readable file")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise HermesSchemaError(f"Hermes {role} file must be UTF-8 Markdown") from error
    heading: str | None = None
    items: list[_ProfileItem] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        heading_match = _HEADING.match(line)
        if heading_match:
            heading = heading_match.group(1).strip()
            continue
        bullet_match = _BULLET.match(line)
        if bullet_match and (content := bullet_match.group(1).strip()):
            items.append(_ProfileItem(role, path.name, heading, line_number, content))
    return raw, tuple(items)


def _profile_sources(
    user_path: Path | None, memory_path: Path | None
) -> tuple[str, tuple[_ProfileItem, ...], dict[str, str]]:
    if user_path is None and memory_path is None:
        raise HermesSchemaError("at least one Hermes USER.md or MEMORY.md path is required")
    digest = hashlib.sha256()
    items: list[_ProfileItem] = []
    filenames: dict[str, str] = {}
    for role, path in (("user", user_path), ("memory", memory_path)):
        if path is None:
            continue
        raw, parsed = _read_profile(path, role)
        role_bytes = role.encode()
        digest.update(len(role_bytes).to_bytes(2, "big"))
        digest.update(role_bytes)
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
        filenames[role] = path.name
        items.extend(parsed)
    return digest.hexdigest(), tuple(items), filenames


def _profile_kind(item: _ProfileItem) -> MemoryKind:
    heading = item.heading or ""
    if _PROJECT_HEADING.search(heading):
        return MemoryKind.PROJECT
    if item.role == "user":
        if re.search(r"\b(?:private|privacy)\b", item.content, re.IGNORECASE):
            return MemoryKind.PREFERENCE
        if _FACT_HEADING.search(heading):
            return MemoryKind.FACT
        if _PREFERENCE_HEADING.search(heading) or not heading:
            return MemoryKind.PREFERENCE
        return MemoryKind.PREFERENCE
    return MemoryKind.FACT


def dry_run_profiles(
    user_path: Path | None = None, memory_path: Path | None = None
) -> HermesProfileDryRun:
    fingerprint, items, _ = _profile_sources(user_path, memory_path)
    return HermesProfileDryRun(
        fingerprint=fingerprint,
        user_count=sum(item.role == "user" for item in items),
        memory_count=sum(item.role == "memory" for item in items),
        sensitive_count=sum(bool(_SENSITIVE.search(item.content)) for item in items),
        candidate_count=len(items),
    )


def stage_profile_records(
    tenant_id: UUID, user_path: Path | None = None, memory_path: Path | None = None
) -> tuple[MigrationBatch, tuple[MemoryRecord, ...]]:
    fingerprint, items, filenames = _profile_sources(user_path, memory_path)
    batch = MigrationBatch(
        tenant_id=tenant_id,
        source_system="hermes_profile_files",
        source_database_fingerprint=fingerprint,
        source_metadata={"wave": 2, "format": "markdown-bullets-v1", "files": filenames},
        persona_draft=None,
    )
    records = tuple(
        MemoryRecord(
            tenant_id=tenant_id,
            kind=_profile_kind(item),
            content=item.content,
            normalized_content=" ".join(item.content.casefold().split()),
            source_event_id=uuid4(),
            source_message_id=uuid4(),
            confidence=0.5,
            confirmed=False,
            state=MemoryState.CANDIDATE,
            sensitivity="high" if _SENSITIVE.search(item.content) else "normal",
            import_batch_id=batch.id,
            source_system="hermes_profile_files",
            source_database_fingerprint=fingerprint,
            source_external_id=f"{item.role}:{item.line_number}",
            source_metadata={
                "wave": 2,
                "source_role": item.role,
                "source_filename": item.filename,
                "heading": item.heading,
                "line_number": item.line_number,
                "source_created_at": None,
                "source_updated_at": None,
                "data_only": True,
            },
        )
        for item in items
    )
    return batch, records


def dry_run(path: Path) -> HermesDryRun:
    fingerprint = _fingerprint(path)
    with _connect(path) as connection:
        _verify(connection)
        facts = connection.execute("SELECT trust_score FROM facts").fetchall()
        scores = [float(row[0]) for row in facts if row[0] is not None]
        entity_count = int(connection.execute("SELECT COUNT(*) FROM entities").fetchone()[0])
    return HermesDryRun(
        fingerprint,
        len(facts),
        entity_count,
        min(scores, default=None),
        max(scores, default=None),
        sum(score < 0.8 for score in scores),
    )


def stage_records(
    path: Path, tenant_id: UUID, persona_draft: PersonaCore | None = None
) -> tuple[MigrationBatch, tuple[MemoryRecord, ...]]:
    fingerprint = _fingerprint(path)
    with _connect(path) as connection:
        _verify(connection)
        rows = connection.execute(
            "SELECT fact_id, content, category, tags, trust_score, retrieval_count, helpful_count, "
            "created_at, updated_at FROM facts ORDER BY fact_id"
        ).fetchall()
        entities: dict[str, list[dict[str, Any]]] = {str(row[0]): [] for row in rows}
        for row in connection.execute(
            "SELECT fe.fact_id, e.name, e.entity_type FROM fact_entities fe "
            "JOIN entities e ON e.entity_id = fe.entity_id ORDER BY fe.fact_id, e.entity_id"
        ):
            entities.setdefault(str(row[0]), []).append({"name": row[1], "type": row[2]})
    batch = MigrationBatch(
        tenant_id=tenant_id,
        source_system="hermes_holographic",
        source_database_fingerprint=fingerprint,
        source_metadata={"layout": "supported-v1"},
        persona_draft=persona_draft,
    )
    records = []
    for row in rows:
        content = str(row[1]).strip()
        score = float(row[4] or 0)
        category = str(row[2] or "")
        kind = (
            MemoryKind.PREFERENCE
            if category == "user_pref"
            else (MemoryKind.PROJECT if category == "project" else MemoryKind.FACT)
        )
        records.append(
            MemoryRecord(
                tenant_id=tenant_id,
                kind=kind,
                content=content,
                normalized_content=" ".join(content.casefold().split()),
                source_event_id=uuid4(),
                source_message_id=uuid4(),
                confidence=max(0, min(1, score)),
                confirmed=False,
                state=MemoryState.PROMOTED if score >= 0.8 else MemoryState.CANDIDATE,
                import_batch_id=batch.id,
                source_system="hermes_holographic",
                source_database_fingerprint=fingerprint,
                source_external_id=str(row[0]),
                source_metadata={
                    "category": category,
                    "tags": row[3],
                    "retrieval_count": row[5],
                    "helpful_count": row[6],
                    "source_created_at": row[7],
                    "source_updated_at": row[8],
                    "entities": entities.get(str(row[0]), []),
                },
            )
        )
    return batch, tuple(records)
