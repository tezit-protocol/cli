"""Tezit Protocol v1.2 bundle generation.

Pure functions for building protocol-compliant manifest.json and tez.md
files. No I/O, no AWS -- just data transformation.
"""

from __future__ import annotations

import re
from typing import Any

TEZIT_VERSION = "1.2"
MANIFEST_FILENAME = "manifest.json"
TEZ_MD_FILENAME = "tez.md"
BUNDLE_FILES: tuple[str, ...] = (MANIFEST_FILENAME, TEZ_MD_FILENAME)

# -----------------------------------------------------------------------
# MIME type -> Tezit Protocol item type mapping
# -----------------------------------------------------------------------

_ITEM_TYPE_MAP: dict[str, str] = {
    # Documents
    "text/markdown": "document",
    "text/plain": "document",
    "application/pdf": "document",
    "application/msword": "document",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "document",  # noqa: E501
    # Data
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "data",
    "application/vnd.ms-excel": "data",
    "text/csv": "data",
    "application/json": "data",
    # Presentations
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "presentation",  # noqa: E501
    "application/vnd.ms-powerpoint": "presentation",
    # Images
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/svg+xml": "image",
    "image/webp": "image",
    # Video
    "video/mp4": "video",
    "video/webm": "video",
    # Audio
    "audio/mpeg": "audio",
    "audio/wav": "audio",
    "audio/ogg": "audio",
    # Code
    "text/x-python": "code",
    "text/javascript": "code",
    "application/javascript": "code",
    "application/x-typescript": "code",
    "text/html": "code",
    "text/css": "code",
    "application/xml": "code",
}


def map_item_type(content_type: str) -> str:
    """Map a MIME content_type to a Tezit Protocol item type.

    Returns "document" for unknown content types.
    """
    return _ITEM_TYPE_MAP.get(content_type, "document")


def slugify_filename(filename: str) -> str:
    """Convert a filename to a Tezit Protocol item ID slug.

    Lowercases, replaces dots, spaces, and slashes with hyphens,
    strips trailing hyphens.

    Examples:
        "transcript.md" -> "transcript-md"
        "action-items.md" -> "action-items-md"
        "My File.docx" -> "my-file-docx"
        "2026-02-05_Onboarding/context.md" -> "2026-02-05_onboarding-context-md"
    """
    slug = filename.lower()
    slug = re.sub(r"[.\s/]+", "-", slug)
    return slug.strip("-")


def build_manifest(
    *,
    tez_id: str,
    name: str,
    description: str,
    creator_name: str,
    creator_email: str,
    created_at: str,
    context_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a Tezit Protocol v1.2-compliant manifest dict.

    Args:
        tez_id: Unique Tez identifier.
        name: Display name / title of the Tez.
        description: Abstract / description.
        creator_name: Creator's display name.
        creator_email: Creator's email address.
        created_at: ISO 8601 creation timestamp.
        context_items: List of context item dicts, each with keys:
            id, type, title, file, size, content_type, and optionally hash.
    """
    return {
        "tezit_version": TEZIT_VERSION,
        "id": tez_id,
        "version": 1,
        "created_at": created_at,
        "creator": {
            "name": creator_name,
            "email": creator_email,
        },
        "synthesis": {
            "title": name,
            "type": "knowledge",
            "file": "tez.md",
            "abstract": description,
        },
        "context": {
            "scope": "private",
            "item_count": len(context_items),
            "items": context_items,
        },
        "permissions": {
            "interrogate": True,
            "fork": True,
            "reshare": False,
        },
    }


def build_context_item(
    *,
    filename: str,
    size: int,
    content_type: str,
    file_hash: str | None = None,
) -> dict[str, Any]:
    """Build a single context item dict for the manifest.

    Args:
        filename: Original filename (e.g. "transcript.md").
        size: File size in bytes.
        content_type: MIME type.
        file_hash: Optional "sha256:{hex}" hash string.
    """
    item: dict[str, Any] = {
        "id": slugify_filename(filename),
        "type": map_item_type(content_type),
        "title": filename,
        "file": f"context/{filename}",
        "size": size,
        "content_type": content_type,
    }
    if file_hash:
        item["hash"] = file_hash
    return item


def build_tez_md(
    *,
    tez_id: str,
    name: str,
    description: str,
    creator_name: str,
    created_at: str,
    context_items: list[dict[str, Any]],
) -> str:
    """Build a Tezit Protocol tez.md with YAML frontmatter.

    The tez.md is the synthesis file -- a human/LLM-readable summary
    of the Tez contents with citation references to context items.

    Args:
        tez_id: Unique Tez identifier.
        name: Display name / title.
        description: Abstract / description.
        creator_name: Creator's display name.
        created_at: ISO 8601 creation timestamp.
        context_items: List of context item dicts (from build_context_item).
    """
    lines = [
        "---",
        f'tezit_version: "{TEZIT_VERSION}"',
        f"id: {tez_id}",
        f"title: {name}",
        f"created_at: {created_at}",
        f"creator: {creator_name}",
        "type: knowledge",
        "---",
        "",
        f"# {name}",
        "",
    ]

    if description:
        lines.append(description)
        lines.append("")

    lines.extend(
        [
            "## Context",
            "",
            f"This Tez contains {len(context_items)} items:",
            "",
            "| Item | Type | Size |",
            "|------|------|------|",
        ]
    )

    for item in context_items:
        item_id = item["id"]
        title = item["title"]
        item_type = item["type"]
        size = _human_size(item["size"])
        lines.append(f"| [[{item_id}]] {title} | {item_type} | {size} |")

    lines.append("")
    return "\n".join(lines)


def _human_size(size: int) -> str:
    """Format byte count as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size //= 1024
    return f"{size} TB"
