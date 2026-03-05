"""Tests for Tezit Protocol bundle generation."""

from __future__ import annotations

import json

from tez.bundle import (
    _human_size,
    build_context_item,
    build_manifest,
    build_tez_md,
    map_item_type,
    slugify_filename,
)


# ===================================================================
# slugify_filename
# ===================================================================
class TestSlugifyFilename:
    def test_simple_extension(self) -> None:
        assert slugify_filename("transcript.md") == "transcript-md"

    def test_hyphenated_name(self) -> None:
        assert slugify_filename("action-items.md") == "action-items-md"

    def test_pdf(self) -> None:
        assert slugify_filename("slides.pdf") == "slides-pdf"

    def test_spaces_become_hyphens(self) -> None:
        assert slugify_filename("My File.docx") == "my-file-docx"

    def test_multiple_dots(self) -> None:
        assert slugify_filename("archive.tar.gz") == "archive-tar-gz"

    def test_uppercase(self) -> None:
        assert slugify_filename("README.MD") == "readme-md"

    def test_no_extension(self) -> None:
        assert slugify_filename("Makefile") == "makefile"

    def test_single_subdirectory(self) -> None:
        assert slugify_filename("subdir/file.md") == "subdir-file-md"

    def test_multi_level_path(self) -> None:
        assert slugify_filename("a/b/c.md") == "a-b-c-md"

    def test_dated_subdirectory(self) -> None:
        assert (
            slugify_filename("2026-02-05_Onboarding/context.md")
            == "2026-02-05_onboarding-context-md"
        )


# ===================================================================
# map_item_type
# ===================================================================
class TestMapItemType:
    def test_markdown_is_document(self) -> None:
        assert map_item_type("text/markdown") == "document"

    def test_pdf_is_document(self) -> None:
        assert map_item_type("application/pdf") == "document"

    def test_csv_is_data(self) -> None:
        assert map_item_type("text/csv") == "data"

    def test_json_is_data(self) -> None:
        assert map_item_type("application/json") == "data"

    def test_png_is_image(self) -> None:
        assert map_item_type("image/png") == "image"

    def test_mp4_is_video(self) -> None:
        assert map_item_type("video/mp4") == "video"

    def test_mpeg_is_audio(self) -> None:
        assert map_item_type("audio/mpeg") == "audio"

    def test_python_is_code(self) -> None:
        assert map_item_type("text/x-python") == "code"

    def test_powerpoint_is_presentation(self) -> None:
        assert map_item_type("application/vnd.ms-powerpoint") == "presentation"

    def test_unknown_defaults_to_document(self) -> None:
        assert map_item_type("application/octet-stream") == "document"

    def test_completely_unknown_defaults_to_document(self) -> None:
        assert map_item_type("application/x-custom-thing") == "document"


# ===================================================================
# build_context_item
# ===================================================================
class TestBuildContextItem:
    def test_builds_item_with_required_fields(self) -> None:
        item = build_context_item(
            filename="notes.md",
            size=1024,
            content_type="text/markdown",
        )
        assert item["id"] == "notes-md"
        assert item["type"] == "document"
        assert item["title"] == "notes.md"
        assert item["file"] == "context/notes.md"
        assert item["size"] == 1024
        assert item["content_type"] == "text/markdown"
        assert "hash" not in item

    def test_includes_hash_when_provided(self) -> None:
        item = build_context_item(
            filename="notes.md",
            size=1024,
            content_type="text/markdown",
            file_hash="sha256:abc123def456",
        )
        assert item["hash"] == "sha256:abc123def456"

    def test_path_based_filename(self) -> None:
        item = build_context_item(
            filename="2026-02-05/transcript.md",
            size=2048,
            content_type="text/markdown",
        )
        assert item["id"] == "2026-02-05-transcript-md"
        assert item["file"] == "context/2026-02-05/transcript.md"
        assert item["title"] == "2026-02-05/transcript.md"

    def test_omits_hash_when_none(self) -> None:
        item = build_context_item(
            filename="notes.md",
            size=1024,
            content_type="text/markdown",
            file_hash=None,
        )
        assert "hash" not in item


# ===================================================================
# build_manifest
# ===================================================================
class TestBuildManifest:
    def _sample_items(self) -> list[dict]:
        return [
            build_context_item(
                filename="transcript.md",
                size=24576,
                content_type="text/markdown",
                file_hash="sha256:aaa",
            ),
            build_context_item(
                filename="slides.pdf",
                size=1048576,
                content_type="application/pdf",
            ),
        ]

    def test_includes_tezit_version(self) -> None:
        manifest = build_manifest(
            tez_id="abc12345",
            name="Test Tez",
            description="A test",
            creator_name="Adam Cross",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=self._sample_items(),
        )
        assert manifest["tezit_version"] == "1.2"

    def test_includes_id_and_version(self) -> None:
        manifest = build_manifest(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert manifest["id"] == "abc12345"
        assert manifest["version"] == 1

    def test_creator_object(self) -> None:
        manifest = build_manifest(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam Cross",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert manifest["creator"]["name"] == "Adam Cross"
        assert manifest["creator"]["email"] == "adam@ragu.ai"

    def test_synthesis_object(self) -> None:
        manifest = build_manifest(
            tez_id="abc12345",
            name="Q1 Notes",
            description="Summary of Q1",
            creator_name="Adam",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert manifest["synthesis"]["title"] == "Q1 Notes"
        assert manifest["synthesis"]["type"] == "knowledge"
        assert manifest["synthesis"]["file"] == "tez.md"
        assert manifest["synthesis"]["abstract"] == "Summary of Q1"

    def test_context_object(self) -> None:
        items = self._sample_items()
        manifest = build_manifest(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=items,
        )
        assert manifest["context"]["scope"] == "private"
        assert manifest["context"]["item_count"] == 2
        assert manifest["context"]["items"] == items

    def test_permissions(self) -> None:
        manifest = build_manifest(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert manifest["permissions"]["interrogate"] is True
        assert manifest["permissions"]["fork"] is True
        assert manifest["permissions"]["reshare"] is False

    def test_serialisable_to_json(self) -> None:
        manifest = build_manifest(
            tez_id="abc12345",
            name="Test",
            description="A test",
            creator_name="Adam",
            creator_email="adam@ragu.ai",
            created_at="2026-02-20T10:00:00Z",
            context_items=self._sample_items(),
        )
        raw = json.dumps(manifest)
        parsed = json.loads(raw)
        assert parsed["tezit_version"] == "1.2"
        assert parsed["synthesis"]["title"] == "Test"


# ===================================================================
# build_tez_md
# ===================================================================
class TestBuildTezMd:
    def _sample_items(self) -> list[dict]:
        return [
            build_context_item(
                filename="transcript.md",
                size=24576,
                content_type="text/markdown",
            ),
            build_context_item(
                filename="slides.pdf",
                size=1048576,
                content_type="application/pdf",
            ),
        ]

    def test_starts_with_yaml_frontmatter(self) -> None:
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="A test",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert md.startswith("---\n")
        # Frontmatter closes with ---
        lines = md.split("\n")
        assert lines[0] == "---"
        # Find closing ---
        closing_idx = None
        for i, line in enumerate(lines[1:], start=1):
            if line == "---":
                closing_idx = i
                break
        assert closing_idx is not None

    def test_frontmatter_contains_version(self) -> None:
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert 'tezit_version: "1.2"' in md

    def test_frontmatter_contains_id(self) -> None:
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert "id: abc12345" in md

    def test_contains_heading(self) -> None:
        md = build_tez_md(
            tez_id="abc12345",
            name="Q1 Standup Notes",
            description="",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert "# Q1 Standup Notes" in md

    def test_contains_description(self) -> None:
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="Some important notes",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert "Some important notes" in md

    def test_lists_context_items_with_citations(self) -> None:
        items = self._sample_items()
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=items,
        )
        assert "[[transcript-md]]" in md
        assert "[[slides-pdf]]" in md
        assert "transcript.md" in md
        assert "slides.pdf" in md

    def test_context_table_shows_types(self) -> None:
        items = self._sample_items()
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=items,
        )
        assert "| document |" in md

    def test_item_count_in_text(self) -> None:
        items = self._sample_items()
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam",
            created_at="2026-02-20T10:00:00Z",
            context_items=items,
        )
        assert "2 items" in md

    def test_creator_in_frontmatter(self) -> None:
        md = build_tez_md(
            tez_id="abc12345",
            name="Test",
            description="",
            creator_name="Adam Cross",
            created_at="2026-02-20T10:00:00Z",
            context_items=[],
        )
        assert "creator: Adam Cross" in md


# ===================================================================
# _human_size
# ===================================================================
class TestHumanSize:
    def test_bytes(self) -> None:
        assert _human_size(100) == "100 B"

    def test_kilobytes(self) -> None:
        assert _human_size(1024) == "1.0 KB"

    def test_megabytes(self) -> None:
        assert _human_size(1024 * 1024) == "1.0 MB"

    def test_gigabytes(self) -> None:
        assert _human_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_zero(self) -> None:
        assert _human_size(0) == "0 B"

    def test_terabytes(self) -> None:
        assert _human_size(1024 * 1024 * 1024 * 1024) == "1 TB"
