"""Public generated-wiki boundary shared by MCP retrieval and page reads."""
from pathlib import Path, PurePosixPath

MAX_PAGE_BYTES = 2 * 1024 * 1024
_PRIVATE = {'queries', 'raw', 'uploads', 'conversations', 'reports', 'audit'}
_EXCLUDED = {'index.md', 'overview.md', 'log.md', 'unanswered.md', 'agents.md', 'claude.md'}


def public_page(root: Path, page_id: str) -> Path | None:
    if not isinstance(page_id, str) or not page_id or '\\' in page_id or '\x00' in page_id:
        return None
    relative = PurePosixPath(page_id)
    if (relative.is_absolute() or relative.as_posix() != page_id
            or any(part.startswith('.') or part.lower() in _PRIVATE for part in relative.parts)
            or relative.suffix.lower() != '.md' or relative.name.lower() in _EXCLUDED):
        return None
    root = root.resolve()
    candidate = root
    try:
        for part in relative.parts:
            candidate = candidate / part
            if candidate.is_symlink():
                return None
        candidate.resolve(strict=True).relative_to(root)
        if not candidate.is_file() or candidate.stat().st_size > MAX_PAGE_BYTES:
            return None
    except (OSError, ValueError):
        return None
    return candidate
