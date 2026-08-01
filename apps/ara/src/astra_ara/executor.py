import re
from collections import Counter
from pathlib import Path

from astra_domain import Task


class ARARepositoryExecutor:
    _ignored_parts = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        "__pycache__",
        "dist",
        "build",
        "node_modules",
        "vendor",
    }
    _text_suffixes = {
        ".c",
        ".cpp",
        ".css",
        ".go",
        ".h",
        ".html",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".md",
        ".php",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".txt",
        ".yaml",
        ".yml",
    }

    def __init__(self, repository_root: Path, max_files: int = 2_000, max_bytes: int = 256_000):
        root = repository_root.expanduser().resolve(strict=True)
        if not root.is_dir():
            raise ValueError("ARA repository root must be a directory")
        self.root = root
        self.max_files = max_files
        self.max_bytes = max_bytes

    def execute(self, task: Task) -> str:
        terms = self._search_terms(task.objective)
        files: list[Path] = []
        suffixes: Counter[str] = Counter()
        matches: list[str] = []
        for path in self.root.rglob("*"):
            if len(files) >= self.max_files:
                break
            if not path.is_file() or any(part in self._ignored_parts for part in path.parts):
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(self.root):
                continue
            relative = resolved.relative_to(self.root)
            files.append(relative)
            suffixes[path.suffix or "[none]"] += 1
            if path.suffix.lower() not in self._text_suffixes:
                continue
            try:
                if path.stat().st_size > self.max_bytes:
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            lowered = content.casefold()
            hit_terms = [
                term for term in terms if term in lowered or term in str(relative).casefold()
            ]
            if hit_terms:
                lines = self._matching_lines(content, hit_terms)
                matches.append(f"- {relative} [{', '.join(hit_terms)}]{lines}")
            if len(matches) >= 40:
                break
        language_summary = ", ".join(
            f"{suffix}: {count}" for suffix, count in suffixes.most_common(10)
        )
        findings = "\n".join(matches) if matches else "- No keyword matches found."
        return (
            f"Repository: {self.root.name}\n"
            f"Files inspected: {len(files)}\n"
            f"File types: {language_summary or 'none'}\n"
            f"Search terms: {', '.join(terms)}\n\n"
            f"Relevant findings:\n{findings}"
        )

    @staticmethod
    def _search_terms(objective: str) -> tuple[str, ...]:
        stopwords = {
            "about",
            "analyze",
            "and",
            "code",
            "find",
            "flow",
            "for",
            "how",
            "inspect",
            "repository",
            "repo",
            "research",
            "the",
            "this",
            "understand",
            "what",
        }
        terms = [
            token
            for token in re.findall(r"[a-zA-Z_][a-zA-Z0-9_-]{2,}", objective.casefold())
            if token not in stopwords
        ]
        if "auth" in objective.casefold() or "login" in objective.casefold():
            terms.extend(["auth", "login", "token", "session", "jwt", "password"])
        return tuple(dict.fromkeys(terms))[:12] or ("readme",)

    @staticmethod
    def _matching_lines(content: str, terms: list[str]) -> str:
        excerpts = []
        for number, line in enumerate(content.splitlines(), 1):
            if any(term in line.casefold() for term in terms):
                excerpts.append(f"\n  L{number}: {line.strip()[:180]}")
            if len(excerpts) == 3:
                break
        return "".join(excerpts)
