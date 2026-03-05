"""Tests for tez.cli module.

The CLI has zero AWS dependencies -- all AWS operations go through MCP.
Tests mock _upload_file, _download_file, and _exchange_token (HTTP layer)
and verify local bundle structure and console output.
"""

import json
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from tez.cli import (
    _detect_content_type,
    _download_file,
    _exchange_token,
    _find_matching_file,
    _human_size,
    _match_files_to_keys,
    _path_ends_with,
    _read_auth_email,
    _require_https,
    _upload_file,
    app,
)

runner = CliRunner()


class TestHumanSize:
    def test_bytes(self) -> None:
        assert _human_size(512) == "512 B"

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


class TestRequireHttps:
    def test_accepts_https(self) -> None:
        url = "https://example.com"
        assert _require_https(url) == url

    def test_rejects_non_https(self) -> None:
        url = "https://example.com"
        tampered = url.replace("https://", "not-https://", 1)
        with pytest.raises(ValueError, match="Only HTTPS"):
            _require_https(tampered)

    def test_rejects_http_downgrade(self) -> None:
        url = "https://example.com"
        downgraded = url.replace("https", "http", 1)
        with pytest.raises(ValueError, match="Only HTTPS"):
            _require_https(downgraded)


class TestCliHelp:
    def test_help_shows_all_commands(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "build" in result.output
        assert "download" in result.output
        assert "cache" in result.output
        assert "auth" in result.output


class TestDetectContentType:
    def test_markdown(self) -> None:
        assert _detect_content_type(Path("file.md")) == "text/markdown"

    def test_pdf(self) -> None:
        assert _detect_content_type(Path("file.pdf")) == "application/pdf"

    def test_unknown_extension(self) -> None:
        assert _detect_content_type(Path("file.xyz123")) == "application/octet-stream"

    def test_no_extension(self) -> None:
        result = _detect_content_type(Path("Makefile"))
        assert isinstance(result, str)


class TestReadAuthEmail:
    def test_reads_email(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".tez"
        config_dir.mkdir()
        config = {"email": "a@b.com", "name": "Test User"}
        (config_dir / "config.json").write_text(json.dumps(config))
        with (
            patch("tez.cli.CONFIG_DIR", config_dir),
            patch("tez.cli.CONFIG_FILE", config_dir / "config.json"),
        ):
            assert _read_auth_email() == "a@b.com"

    def test_exits_when_not_logged_in(self, tmp_path: Path) -> None:
        import typer

        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            import pytest

            with pytest.raises(typer.Exit):
                _read_auth_email()


class TestUploadFile:
    @patch("tez.cli.urlopen")
    def test_sends_put_request(self, mock_urlopen: MagicMock, tmp_path: Path) -> None:
        test_file = tmp_path / "test.md"
        test_file.write_text("hello")
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value = mock_resp

        status = _upload_file(
            url="https://s3.amazonaws.com/test",
            file_path=test_file,
            content_type="text/markdown",
        )

        assert status == 200
        mock_urlopen.assert_called_once()
        call_arg = mock_urlopen.call_args[0][0]
        assert call_arg.method == "PUT"
        assert call_arg.get_header("Content-type") == "text/markdown"


class TestDownloadFile:
    @patch("tez.cli.urlopen")
    def test_sends_get_request(self, mock_urlopen: MagicMock, tmp_path: Path) -> None:
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b"file content"
        mock_urlopen.return_value = mock_resp

        dest = tmp_path / "output.md"
        status = _download_file(url="https://s3.amazonaws.com/test", dest=dest)

        assert status == 200
        assert dest.read_bytes() == b"file content"
        mock_urlopen.assert_called_once()


class TestExchangeToken:
    @patch("tez.cli.urlopen")
    def test_returns_payload(self, mock_urlopen: MagicMock) -> None:
        payload = {"upload_urls": {"file.md": "https://s3.example.com"}}
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(payload).encode()
        mock_urlopen.return_value = mock_resp

        result = _exchange_token("https://tez.example.com", "abc123")

        assert result == payload
        call_arg = mock_urlopen.call_args[0][0]
        assert call_arg.full_url == "https://tez.example.com/api/tokens/abc123"

    @patch("tez.cli.urlopen")
    def test_constructs_correct_url(self, mock_urlopen: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"key": "value"}'
        mock_urlopen.return_value = mock_resp

        _exchange_token("https://my-server.com", "tok_xyz")

        call_arg = mock_urlopen.call_args[0][0]
        assert call_arg.full_url == "https://my-server.com/api/tokens/tok_xyz"

    def test_exits_on_network_failure(self) -> None:
        import typer

        with (
            patch("tez.cli.urlopen", side_effect=OSError("Connection refused")),
            pytest.raises(typer.Exit),
        ):
            _exchange_token("https://tez.example.com", "bad-token")

    def test_exits_on_http_error(self) -> None:
        from urllib.error import HTTPError

        import typer

        error = HTTPError(
            url="https://tez.example.com/api/tokens/bad",
            code=404,
            msg="Not Found",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )
        with (
            patch("tez.cli.urlopen", side_effect=error),
            pytest.raises(typer.Exit),
        ):
            _exchange_token("https://tez.example.com", "bad-token")


def _setup_auth(tmp_path: Path) -> Path:
    """Create auth config dir with test email and name."""
    config_dir = tmp_path / ".tez"
    config_dir.mkdir()
    config = {"email": "adam@ragu.ai", "name": "Adam Cross"}
    (config_dir / "config.json").write_text(json.dumps(config))
    return config_dir


@contextmanager
def _build_patches(
    config_dir: Path,
    tez_dir: Path,
    upload_urls: dict[str, str],
    *,
    tez_id: str = "abc123",
    upload_side_effect: object = None,
) -> Generator[None, None, None]:
    """Context manager with common patches for build command tests."""
    token_payload = {"tez_id": tez_id, "upload_urls": upload_urls}
    upload_patch = (
        patch("tez.cli._upload_file", side_effect=upload_side_effect)
        if upload_side_effect is not None
        else patch("tez.cli._upload_file", return_value=200)
    )
    with (
        patch("tez.cli.CONFIG_DIR", config_dir),
        patch("tez.cli.CONFIG_FILE", config_dir / "config.json"),
        patch("tez.cli.TEZ_DIR", tez_dir),
        patch("tez.cli._exchange_token", return_value=token_payload),
        upload_patch,
    ):
        yield


def _build_args(tez_id: str, name: str, desc: str, *files: str) -> list[str]:
    """Build CLI args for the build command with token-based auth."""
    return [
        "build",
        tez_id,
        "--name",
        name,
        "--desc",
        desc,
        "--server",
        "https://tez.example.com",
        "--token",
        "fake-upload-token",
        *files,
    ]


class TestBuildCommand:
    def test_build_creates_local_bundle(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        test_file = tmp_path / "notes.md"
        test_file.write_text("# Meeting notes")

        upload_urls = {
            "notes.md": "https://s3.example.com/abc123/context/notes.md",
            "manifest.json": "https://s3.example.com/abc123/manifest.json",
            "tez.md": "https://s3.example.com/abc123/tez.md",
        }

        with _build_patches(config_dir, tez_dir, upload_urls):
            result = runner.invoke(
                app,
                _build_args("abc123", "Test Tez", "A test", str(test_file)),
            )

        assert result.exit_code == 0, result.output
        assert "Tez ID: abc123" in result.output

        # Verify local bundle structure
        bundle_dir = tez_dir / "abc123"
        assert (bundle_dir / "context" / "notes.md").exists()
        assert (bundle_dir / "manifest.json").exists()
        assert (bundle_dir / "tez.md").exists()

        # Verify manifest is valid JSON with protocol fields
        manifest = json.loads((bundle_dir / "manifest.json").read_text())
        assert manifest["tezit_version"] == "1.2"
        assert manifest["id"] == "abc123"

    def test_build_multiple_files(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        file_a = tmp_path / "a.md"
        file_a.write_text("file a")
        file_b = tmp_path / "b.pdf"
        file_b.write_bytes(b"%PDF-1.4 fake")

        upload_urls = {
            "a.md": "https://s3.example.com/xyz/context/a.md",
            "b.pdf": "https://s3.example.com/xyz/context/b.pdf",
            "manifest.json": "https://s3.example.com/xyz/manifest.json",
            "tez.md": "https://s3.example.com/xyz/tez.md",
        }

        with _build_patches(config_dir, tez_dir, upload_urls, tez_id="xyz"):
            result = runner.invoke(
                app,
                _build_args("xyz", "Multi", "Two files", str(file_a), str(file_b)),
            )

        assert result.exit_code == 0, result.output
        assert (tez_dir / "xyz" / "context" / "a.md").exists()
        assert (tez_dir / "xyz" / "context" / "b.pdf").exists()

    def test_build_not_logged_in(self, tmp_path: Path) -> None:
        test_file = tmp_path / "notes.md"
        test_file.write_text("hello")

        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            result = runner.invoke(
                app,
                _build_args("abc123", "T", "D", str(test_file)),
            )

        assert result.exit_code == 1
        assert "Not logged in" in result.output

    def test_build_upload_failure(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        test_file = tmp_path / "notes.md"
        test_file.write_text("hello")

        upload_urls = {
            "notes.md": "https://s3.example.com/abc123/context/notes.md",
            "manifest.json": "https://s3.example.com/abc123/manifest.json",
            "tez.md": "https://s3.example.com/abc123/tez.md",
        }

        with _build_patches(
            config_dir,
            tez_dir,
            upload_urls,
            upload_side_effect=OSError("Connection refused"),
        ):
            result = runner.invoke(
                app,
                _build_args("abc123", "T", "D", str(test_file)),
            )

        assert result.exit_code == 1
        assert "Upload failed" in result.output

    def test_build_missing_upload_url(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        test_file = tmp_path / "notes.md"
        test_file.write_text("hello")

        # Missing URL for notes.md
        upload_urls = {
            "manifest.json": "https://s3.example.com/abc123/manifest.json",
            "tez.md": "https://s3.example.com/abc123/tez.md",
        }

        with _build_patches(config_dir, tez_dir, upload_urls):
            result = runner.invoke(
                app,
                _build_args("abc123", "T", "D", str(test_file)),
            )

        assert result.exit_code == 1
        assert "No upload URL" in result.output

    def test_build_missing_manifest_upload_url(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        test_file = tmp_path / "notes.md"
        test_file.write_text("hello")

        # Has context file URL but missing manifest.json URL
        upload_urls = {
            "notes.md": "https://s3.example.com/abc123/context/notes.md",
            "tez.md": "https://s3.example.com/abc123/tez.md",
        }

        with _build_patches(config_dir, tez_dir, upload_urls):
            result = runner.invoke(
                app,
                _build_args("abc123", "T", "D", str(test_file)),
            )

        assert result.exit_code == 1
        assert "No upload URL" in result.output

    def test_build_manifest_upload_failure(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        test_file = tmp_path / "notes.md"
        test_file.write_text("hello")

        upload_urls = {
            "notes.md": "https://s3.example.com/abc123/context/notes.md",
            "manifest.json": "https://s3.example.com/abc123/manifest.json",
            "tez.md": "https://s3.example.com/abc123/tez.md",
        }

        call_count = 0

        def upload_fails_on_manifest(
            url: str, file_path: Path, content_type: str
        ) -> int:
            nonlocal call_count
            call_count += 1
            # First call is context file, second is manifest.json -- fail there
            if call_count > 1:
                raise OSError("S3 timeout")
            return 200

        with _build_patches(
            config_dir,
            tez_dir,
            upload_urls,
            upload_side_effect=upload_fails_on_manifest,
        ):
            result = runner.invoke(
                app,
                _build_args("abc123", "T", "D", str(test_file)),
            )

        assert result.exit_code == 1
        assert "Upload failed" in result.output

    def test_build_manifest_has_sha256_hashes(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"
        test_file = tmp_path / "notes.md"
        test_file.write_text("hello")

        upload_urls = {
            "notes.md": "https://s3.example.com/abc123/context/notes.md",
            "manifest.json": "https://s3.example.com/abc123/manifest.json",
            "tez.md": "https://s3.example.com/abc123/tez.md",
        }

        with _build_patches(config_dir, tez_dir, upload_urls):
            runner.invoke(
                app,
                _build_args("abc123", "Hash Test", "Test hashes", str(test_file)),
            )

        manifest = json.loads((tez_dir / "abc123" / "manifest.json").read_text())
        items = manifest["context"]["items"]
        assert len(items) == 1
        assert items[0]["hash"].startswith("sha256:")


@contextmanager
def _download_patches(
    tez_dir: Path,
    download_urls: dict[str, str],
    *,
    tez_id: str = "dl-happy",
    download_side_effect: object = None,
) -> Generator[None, None, None]:
    """Context manager with common patches for download command tests."""
    token_payload = {"tez_id": tez_id, "download_urls": download_urls}
    if download_side_effect is not None:
        with (
            patch("tez.cli.TEZ_DIR", tez_dir),
            patch("tez.cli._exchange_token", return_value=token_payload),
            patch("tez.cli._download_file", side_effect=download_side_effect),
        ):
            yield
    else:
        with (
            patch("tez.cli.TEZ_DIR", tez_dir),
            patch("tez.cli._exchange_token", return_value=token_payload),
        ):
            yield


def _download_args(tez_id: str) -> list[str]:
    """Build CLI args for the download command with token-based auth."""
    return [
        "download",
        tez_id,
        "--server",
        "https://tez.example.com",
        "--token",
        "fake-download-token",
    ]


class TestDownloadCommand:
    def test_download_creates_protocol_structure(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"

        download_urls = {
            "notes.md": "https://s3.example.com/dl-happy/context/notes.md",
            "manifest.json": "https://s3.example.com/dl-happy/manifest.json",
            "tez.md": "https://s3.example.com/dl-happy/tez.md",
        }

        def fake_download(url: str, dest: Path) -> int:
            if "notes.md" in url:
                dest.write_text("# Notes")
            elif "manifest.json" in url:
                dest.write_text('{"tezit_version": "1.2"}')
            elif "tez.md" in url:
                dest.write_text("# Test Tez")
            return 200

        with _download_patches(
            tez_dir, download_urls, download_side_effect=fake_download
        ):
            result = runner.invoke(app, _download_args("dl-happy"))

        assert result.exit_code == 0, result.output
        assert "Done." in result.output

        # Verify protocol bundle structure
        bundle_dir = tez_dir / "dl-happy"
        assert (bundle_dir / "context" / "notes.md").exists()
        assert (bundle_dir / "manifest.json").exists()
        assert (bundle_dir / "tez.md").exists()
        assert (bundle_dir / "context" / "notes.md").read_text() == "# Notes"

    def test_download_failure(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"

        download_urls = {
            "notes.md": "https://s3.example.com/dl-fail/context/notes.md",
        }

        with _download_patches(
            tez_dir,
            download_urls,
            tez_id="dl-fail",
            download_side_effect=OSError("Connection refused"),
        ):
            result = runner.invoke(app, _download_args("dl-fail"))

        assert result.exit_code == 1
        assert "Download failed" in result.output

    def test_download_multiple_context_files(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"

        download_urls = {
            "a.md": "https://s3.example.com/dl-multi/context/a.md",
            "b.pdf": "https://s3.example.com/dl-multi/context/b.pdf",
            "manifest.json": "https://s3.example.com/dl-multi/manifest.json",
            "tez.md": "https://s3.example.com/dl-multi/tez.md",
        }

        def fake_download(url: str, dest: Path) -> int:
            dest.write_bytes(b"content")
            return 200

        with _download_patches(
            tez_dir,
            download_urls,
            tez_id="dl-multi",
            download_side_effect=fake_download,
        ):
            result = runner.invoke(app, _download_args("dl-multi"))

        assert result.exit_code == 0, result.output
        assert (tez_dir / "dl-multi" / "context" / "a.md").exists()
        assert (tez_dir / "dl-multi" / "context" / "b.pdf").exists()
        assert (tez_dir / "dl-multi" / "manifest.json").exists()
        assert (tez_dir / "dl-multi" / "tez.md").exists()


class TestAuthLogin:
    def test_login_saves_config(self, tmp_path: Path) -> None:
        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            result = runner.invoke(
                app,
                ["auth", "login", "--email", "adam@ragu.ai", "--name", "Adam Cross"],
            )
        assert result.exit_code == 0
        assert "Adam Cross" in result.output
        assert "adam@ragu.ai" in result.output

        config = json.loads((tmp_path / "config.json").read_text())
        assert config["email"] == "adam@ragu.ai"
        assert config["name"] == "Adam Cross"

    def test_login_creates_config_dir(self, tmp_path: Path) -> None:
        nested = tmp_path / "sub" / ".tez"
        with (
            patch("tez.cli.CONFIG_DIR", nested),
            patch("tez.cli.CONFIG_FILE", nested / "config.json"),
        ):
            result = runner.invoke(
                app,
                ["auth", "login", "--email", "t@ragu.ai", "--name", "Test"],
            )
        assert result.exit_code == 0
        assert nested.exists()


class TestAuthWhoami:
    def test_whoami_shows_name_and_email(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config = {"email": "adam@ragu.ai", "name": "Adam Cross"}
        config_file.write_text(json.dumps(config))

        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            result = runner.invoke(app, ["auth", "whoami"])
        assert result.exit_code == 0
        assert "Adam Cross" in result.output
        assert "adam@ragu.ai" in result.output

    def test_whoami_not_logged_in(self, tmp_path: Path) -> None:
        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            result = runner.invoke(app, ["auth", "whoami"])
        assert result.exit_code == 1
        assert "Not logged in" in result.output


class TestAuthLogout:
    def test_logout_removes_config(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"email": "adam@ragu.ai"}))

        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            result = runner.invoke(app, ["auth", "logout"])
        assert result.exit_code == 0
        assert "Logged out" in result.output
        assert not config_file.exists()

    def test_logout_when_not_logged_in(self, tmp_path: Path) -> None:
        with (
            patch("tez.cli.CONFIG_DIR", tmp_path),
            patch("tez.cli.CONFIG_FILE", tmp_path / "config.json"),
        ):
            result = runner.invoke(app, ["auth", "logout"])
        assert result.exit_code == 0
        assert "Not logged in" in result.output


class TestDownloadManifestErrors:
    def test_download_skips_missing_manifest_url(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"

        # Only context file, no manifest.json or tez.md URLs
        download_urls = {
            "notes.md": "https://s3.example.com/dl/context/notes.md",
        }

        def fake_download(url: str, dest: Path) -> int:
            dest.write_bytes(b"content")
            return 200

        with _download_patches(
            tez_dir, download_urls, tez_id="dl-skip", download_side_effect=fake_download
        ):
            result = runner.invoke(app, _download_args("dl-skip"))

        assert result.exit_code == 0
        assert (tez_dir / "dl-skip" / "context" / "notes.md").exists()
        # Manifest files not downloaded since no URLs
        assert not (tez_dir / "dl-skip" / "manifest.json").exists()

    def test_download_manifest_failure(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"

        download_urls = {
            "manifest.json": "https://s3.example.com/dl/manifest.json",
            "tez.md": "https://s3.example.com/dl/tez.md",
        }

        with _download_patches(
            tez_dir,
            download_urls,
            tez_id="dl-mfail",
            download_side_effect=OSError("Connection reset"),
        ):
            result = runner.invoke(app, _download_args("dl-mfail"))

        assert result.exit_code == 1
        assert "Download failed" in result.output


class TestCacheCleanCommand:
    def test_cache_clean_no_files(self) -> None:
        result = runner.invoke(app, ["cache", "clean", "nonexistent-id"])
        assert result.exit_code == 0
        assert "No cached files" in result.output

    def test_cache_clean_existing_dir(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"
        cached = tez_dir / "abc123" / "context"
        cached.mkdir(parents=True)
        (cached / "notes.md").write_text("cached")

        with patch("tez.cli.TEZ_DIR", tez_dir):
            result = runner.invoke(app, ["cache", "clean", "abc123"])

        assert result.exit_code == 0
        assert "Removed" in result.output
        assert not (tez_dir / "abc123").exists()


class TestPathEndsWith:
    def test_exact_basename_match(self) -> None:
        assert _path_ends_with(Path("/abs/notes.md"), "notes.md") is True

    def test_subdirectory_match(self) -> None:
        assert (
            _path_ends_with(Path("/abs/calls/2026-02-05/ctx.md"), "2026-02-05/ctx.md")
            is True
        )

    def test_no_match(self) -> None:
        assert _path_ends_with(Path("/abs/other.md"), "notes.md") is False

    def test_partial_name_no_match(self) -> None:
        # "otes.md" is a suffix of "notes.md" but not at a path boundary
        assert _path_ends_with(Path("/abs/notes.md"), "otes.md") is False

    def test_suffix_longer_than_path(self) -> None:
        assert _path_ends_with(Path("a.md"), "long/path/a.md") is False

    def test_multi_level_subdirectory(self) -> None:
        assert (
            _path_ends_with(
                Path("/home/user/calls/drata/2026-02-05/transcript.md"),
                "drata/2026-02-05/transcript.md",
            )
            is True
        )


class TestFindMatchingFile:
    def test_finds_match_by_suffix(self) -> None:
        files = [Path("/tmp/a.md"), Path("/tmp/b.md")]
        used: set[int] = set()
        result = _find_matching_file("b.md", files, used)
        assert result == Path("/tmp/b.md")
        assert 1 in used

    def test_returns_none_when_no_match(self) -> None:
        files = [Path("/tmp/a.md")]
        used: set[int] = set()
        result = _find_matching_file("missing.md", files, used)
        assert result is None
        assert len(used) == 0

    def test_skips_already_used_files(self) -> None:
        files = [Path("/tmp/a.md"), Path("/tmp/b.md")]
        used: set[int] = {0}
        result = _find_matching_file("a.md", files, used)
        assert result is None

    def test_matches_subdirectory_key(self) -> None:
        files = [Path("/abs/calls/2026-02-05/ctx.md")]
        used: set[int] = set()
        result = _find_matching_file("2026-02-05/ctx.md", files, used)
        assert result == Path("/abs/calls/2026-02-05/ctx.md")
        assert 0 in used


class TestMatchFilesToKeys:
    def test_flat_files(self) -> None:
        local = [Path("/tmp/notes.md"), Path("/tmp/slides.pdf")]
        urls = {
            "notes.md": "https://s3/notes.md",
            "slides.pdf": "https://s3/slides.pdf",
            "manifest.json": "https://s3/manifest.json",
            "tez.md": "https://s3/tez.md",
        }
        matched = _match_files_to_keys(local, urls)
        assert len(matched) == 2
        bundle_names = {m[1] for m in matched}
        assert bundle_names == {"notes.md", "slides.pdf"}

    def test_subdirectory_keys(self) -> None:
        local = [
            Path("/home/calls/2026-02-05/context.md"),
            Path("/home/calls/2026-02-16/context.md"),
        ]
        urls = {
            "2026-02-05/context.md": "https://s3/a",
            "2026-02-16/context.md": "https://s3/b",
            "manifest.json": "https://s3/m",
            "tez.md": "https://s3/t",
        }
        matched = _match_files_to_keys(local, urls)
        assert len(matched) == 2
        bundle_names = {m[1] for m in matched}
        assert bundle_names == {"2026-02-05/context.md", "2026-02-16/context.md"}

    def test_unmatched_key_exits(self) -> None:
        import typer

        local = [Path("/tmp/notes.md")]
        urls = {
            "missing-file.md": "https://s3/missing",
            "manifest.json": "https://s3/m",
            "tez.md": "https://s3/t",
        }
        with pytest.raises(typer.Exit):
            _match_files_to_keys(local, urls)


class TestBuildSubdirectoryStructure:
    def test_build_preserves_subdirectory_structure(self, tmp_path: Path) -> None:
        config_dir = _setup_auth(tmp_path)
        tez_dir = tmp_path / "tez"

        # Create files in subdirectories
        sub_a = tmp_path / "calls" / "2026-02-05"
        sub_b = tmp_path / "calls" / "2026-02-16"
        sub_a.mkdir(parents=True)
        sub_b.mkdir(parents=True)
        file_a = sub_a / "transcript.md"
        file_a.write_text("Onboarding call")
        file_b = sub_b / "transcript.md"
        file_b.write_text("Dylan ISO call")

        upload_urls = {
            "2026-02-05/transcript.md": "https://s3/ctx/a",
            "2026-02-16/transcript.md": "https://s3/ctx/b",
            "manifest.json": "https://s3/manifest",
            "tez.md": "https://s3/tez",
        }

        with _build_patches(config_dir, tez_dir, upload_urls):
            result = runner.invoke(
                app,
                _build_args(
                    "abc123",
                    "Calls",
                    "Two calls",
                    str(file_a),
                    str(file_b),
                ),
            )

        assert result.exit_code == 0, result.output

        # Verify subdirectory structure preserved in context/
        bundle = tez_dir / "abc123"
        assert (bundle / "context" / "2026-02-05" / "transcript.md").exists()
        assert (bundle / "context" / "2026-02-16" / "transcript.md").exists()

        # Verify manifest uses path-based filenames
        manifest = json.loads((bundle / "manifest.json").read_text())
        items = manifest["context"]["items"]
        filenames = {item["file"] for item in items}
        assert "context/2026-02-05/transcript.md" in filenames
        assert "context/2026-02-16/transcript.md" in filenames

        # Verify unique IDs (not both "transcript-md")
        ids = {item["id"] for item in items}
        assert len(ids) == 2


class TestDownloadSubdirectoryStructure:
    def test_download_creates_subdirectories(self, tmp_path: Path) -> None:
        tez_dir = tmp_path / "tez"

        download_urls = {
            "2026-02-05/transcript.md": "https://s3/a",
            "2026-02-16/transcript.md": "https://s3/b",
            "manifest.json": "https://s3/manifest",
            "tez.md": "https://s3/tez",
        }

        def fake_download(url: str, dest: Path) -> int:
            dest.write_text("content")
            return 200

        with _download_patches(
            tez_dir,
            download_urls,
            tez_id="dl-sub",
            download_side_effect=fake_download,
        ):
            result = runner.invoke(app, _download_args("dl-sub"))

        assert result.exit_code == 0, result.output
        ctx = tez_dir / "dl-sub" / "context"
        assert (ctx / "2026-02-05" / "transcript.md").exists()
        assert (ctx / "2026-02-16" / "transcript.md").exists()


class TestMainEntryPoint:
    def test_main_invokes_app(self) -> None:
        from tez.cli import main

        with patch("tez.cli.app") as mock_app:
            main()
            mock_app.assert_called_once()
