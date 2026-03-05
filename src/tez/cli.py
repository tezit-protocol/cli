"""Tez CLI -- local file I/O for context packages.

The CLI handles what MCP cannot: local file operations and HTTP transfers
using pre-signed URLs. It has zero AWS credentials -- all AWS operations
(DynamoDB, S3 URL generation) go through MCP tools.
"""

import hashlib
import json
import mimetypes
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import typer

from tez.bundle import (
    BUNDLE_FILES,
    MANIFEST_FILENAME,
    TEZ_MD_FILENAME,
    build_context_item,
    build_manifest,
    build_tez_md,
)

app = typer.Typer(
    name="tez",
    help=(
        "Tez CLI -- local file I/O for scoped context packages.\n\n"
        "The CLI works together with MCP tools. Claude calls MCP first\n"
        "(for IDs, tokens, metadata), then runs CLI commands for file I/O.\n"
        "The CLI has zero AWS credentials -- it exchanges short-lived tokens\n"
        "for pre-signed S3 URLs via the Tez server REST API.\n\n"
        "Flows:\n\n"
        "  Build:    MCP tez_build -> CLI tez build -> MCP tez_build_confirm\n\n"
        "  Download: MCP tez_download -> CLI tez download\n\n"
        "  Share/List/Info/Delete: MCP only (no CLI needed)\n\n"
        "Auth:\n\n"
        "  tez auth login --email <email> --name <name>\n\n"
        "  tez auth whoami\n\n"
        "  tez auth logout"
    ),
    no_args_is_help=True,
)

cache_app = typer.Typer(help="Manage local Tez cache.")
app.add_typer(cache_app, name="cache")

auth_app = typer.Typer(help="Manage authentication.")
app.add_typer(auth_app, name="auth")


TEZ_DIR = Path(tempfile.gettempdir()) / "tez"
CONFIG_DIR = Path.home() / ".tez"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _read_auth_config() -> dict[str, str]:
    """Read auth config from ~/.tez/config.json, or exit."""
    config_file = CONFIG_FILE
    if not config_file.exists():
        typer.secho(
            "  Error: Not logged in. Run: tez auth login",
            fg="red",
        )
        raise typer.Exit(1)
    config: dict[str, str] = json.loads(config_file.read_text())
    return config


def _detect_content_type(file_path: Path) -> str:
    """Guess MIME type from file extension, defaulting to application/octet-stream."""
    mime_type, _ = mimetypes.guess_type(str(file_path))
    return mime_type or "application/octet-stream"


def _require_https(url: str) -> str:
    """Validate that a URL uses HTTPS. Raises ValueError otherwise."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        msg = f"Only HTTPS URLs are allowed, got {parsed.scheme!r}"
        raise ValueError(msg)
    return url


def _upload_file(url: str, file_path: Path, content_type: str) -> int:
    """HTTP PUT a file to a pre-signed S3 URL. Returns HTTP status code."""
    data = file_path.read_bytes()
    req = Request(_require_https(url), data=data, method="PUT")
    req.add_header("Content-Type", content_type)
    resp = urlopen(req)
    status: int = resp.status
    return status


def _path_ends_with(file_path: Path, suffix_key: str) -> bool:
    """Check if a local file path's trailing parts match a URL key.

    Converts both sides to forward-slash strings and checks whether
    the local path ends with the suffix key.

    Examples:
        Path("/abs/calls/2026-02-05/ctx.md"), "2026-02-05/ctx.md" -> True
        Path("/abs/notes.md"), "notes.md" -> True
        Path("/abs/other.md"), "notes.md" -> False
    """
    normalised = file_path.as_posix()
    if normalised.endswith(suffix_key):
        prefix_len = len(normalised) - len(suffix_key)
        return prefix_len == 0 or normalised[prefix_len - 1] == "/"
    return False


def _find_matching_file(
    key: str,
    local_files: list[Path],
    used_files: set[int],
) -> Path | None:
    """Find the first unused local file whose path ends with the given key."""
    for idx, fp in enumerate(local_files):
        if idx in used_files:
            continue
        if _path_ends_with(fp, key):
            used_files.add(idx)
            return fp
    return None


def _match_files_to_keys(
    local_files: list[Path],
    upload_urls: dict[str, str],
) -> list[tuple[Path, str, str]]:
    """Match upload URL keys to local files by path suffix.

    For each upload URL key (excluding manifest entries), finds the local
    file whose path ends with that key. Returns a list of
    (local_path, bundle_name, url) tuples where bundle_name is the URL
    key (used as the relative path inside context/).

    Raises typer.Exit(1) if any non-manifest key cannot be matched.
    """
    manifest_keys = set(BUNDLE_FILES)
    matched: list[tuple[Path, str, str]] = []
    used_files: set[int] = set()

    for key, url in upload_urls.items():
        if key in manifest_keys:
            continue
        fp = _find_matching_file(key, local_files, used_files)
        if fp is None:
            typer.secho(f"  No local file matches upload key '{key}'", fg="red")
            raise typer.Exit(1)
        matched.append((fp, key, url))

    for idx, fp in enumerate(local_files):
        if idx not in used_files:
            typer.secho(f"  No upload URL for {fp.name}", fg="red")
            raise typer.Exit(1)

    return matched


def _exchange_token(server: str, token: str) -> dict[str, Any]:
    """Exchange a single-use token for pre-signed URLs via the server REST API.

    Args:
        server: Base URL of the Tez server (e.g. "https://tez.example.com").
        token: The opaque token string from MCP.

    Returns:
        The token payload dict containing the URLs.

    Raises:
        typer.Exit: If the exchange fails (expired, invalid, network error).
    """
    url = f"{server}/api/tokens/{token}"
    req = Request(url)
    try:
        resp = urlopen(req)
        data: dict[str, Any] = json.loads(resp.read())
        return data
    except Exception as exc:
        typer.secho(f"  Token exchange failed: {exc}", fg="red")
        raise typer.Exit(1) from exc


@app.command()
def build(
    tez_id: Annotated[str, typer.Argument(help="Tez ID (from MCP tez_build)")],
    name: Annotated[str, typer.Option("--name", "-n", help="Name of the Tez")],
    description: Annotated[
        str, typer.Option("--desc", "-d", help="Description of the Tez")
    ],
    server: Annotated[str, typer.Option("--server", "-s", help="Tez server URL")],
    token: Annotated[
        str,
        typer.Option("--token", "-t", help="Upload token from MCP tez_build"),
    ],
    files: Annotated[
        list[Path],
        typer.Argument(help="Files to include in the Tez", exists=True),
    ],
) -> None:
    """Build a local protocol bundle and upload to S3.

    Phase 2 of the build flow. Claude calls MCP tez_build first to get
    a tez_id, upload_token, and server URL. Then runs this command:

      tez build <tez_id> --name "X" --desc "Y" \\
        --server <server> --token <token> file1 file2

    The CLI exchanges the token for pre-signed PUT URLs via
    GET <server>/api/tokens/<token>, assembles a Tezit Protocol v1.2
    bundle in /tmp/tez/{tez_id}/, and uploads all files.

    After this command succeeds, Claude calls MCP tez_build_confirm
    to activate the Tez.
    """
    auth = _read_auth_config()
    creator_email = auth["email"]
    creator_name = auth["name"]
    now = datetime.now(tz=UTC).isoformat()

    token_data = _exchange_token(server, token)
    upload_urls: dict[str, str] = token_data["upload_urls"]

    matched = _match_files_to_keys(files, upload_urls)

    bundle_dir = TEZ_DIR / tez_id
    context_dir = bundle_dir / "context"
    context_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"  Building Tez '{name}' -> {bundle_dir}/")

    context_items: list[dict[str, Any]] = []
    for local_path, bundle_name, _url in matched:
        dest = context_dir / bundle_name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(local_path, dest)

        file_bytes = local_path.read_bytes()
        sha256_hash = hashlib.sha256(file_bytes).hexdigest()

        item = build_context_item(
            filename=bundle_name,
            size=local_path.stat().st_size,
            content_type=_detect_content_type(local_path),
            file_hash=f"sha256:{sha256_hash}",
        )
        context_items.append(item)
        typer.echo(f"    + {bundle_name} ({_human_size(local_path.stat().st_size)})")

    manifest = build_manifest(
        tez_id=tez_id,
        name=name,
        description=description,
        creator_name=creator_name,
        creator_email=creator_email,
        created_at=now,
        context_items=context_items,
    )
    manifest_path = bundle_dir / MANIFEST_FILENAME
    manifest_path.write_text(json.dumps(manifest, indent=2))

    tez_md = build_tez_md(
        tez_id=tez_id,
        name=name,
        description=description,
        creator_name=creator_name,
        created_at=now,
        context_items=context_items,
    )
    tez_md_path = bundle_dir / TEZ_MD_FILENAME
    tez_md_path.write_text(tez_md)

    typer.echo("  Bundle assembled locally")

    typer.echo("  Uploading...")

    for _local_path, bundle_name, url in matched:
        typer.echo(f"    - {bundle_name} ...", nl=False)
        try:
            _upload_file(
                url=url,
                file_path=context_dir / bundle_name,
                content_type=_detect_content_type(context_dir / bundle_name),
            )
            typer.echo(" done")
        except OSError as exc:
            typer.echo()
            typer.secho(f"  Upload failed for {bundle_name}: {exc}", fg="red")
            raise typer.Exit(1) from exc

    for name_key, path, ct in (
        (MANIFEST_FILENAME, manifest_path, "application/json"),
        (TEZ_MD_FILENAME, tez_md_path, "text/markdown"),
    ):
        manifest_url = upload_urls.get(name_key)
        if not manifest_url:
            typer.secho(f"  No upload URL for {name_key}", fg="red")
            raise typer.Exit(1)
        try:
            _upload_file(url=manifest_url, file_path=path, content_type=ct)
        except OSError as exc:
            typer.secho(f"  Upload failed for {name_key}: {exc}", fg="red")
            raise typer.Exit(1) from exc

    typer.echo()
    typer.secho(f"  Tez ID: {tez_id}", bold=True)
    typer.secho(f"  Bundle: {bundle_dir}/", bold=True)


def _download_file(url: str, dest: Path) -> int:
    """HTTP GET a file from a pre-signed S3 URL and save to disk."""
    req = Request(_require_https(url))
    resp = urlopen(req)
    dest.write_bytes(resp.read())
    status: int = resp.status
    return status


@app.command()
def download(
    tez_id: Annotated[str, typer.Argument(help="ID of the Tez to download")],
    server: Annotated[str, typer.Option("--server", "-s", help="Tez server URL")],
    token: Annotated[
        str,
        typer.Option("--token", "-t", help="Download token from MCP tez_download"),
    ],
) -> None:
    """Download a Tez bundle to local cache.

    Phase 2 of the download flow. Claude calls MCP tez_download first
    to get a download_token and server URL. Then runs this command:

      tez download <tez_id> --server <server> --token <token>

    The CLI exchanges the token for pre-signed GET URLs via
    GET <server>/api/tokens/<token>, then downloads all files into
    /tmp/tez/{tez_id}/, producing a protocol-compliant local bundle.
    """
    token_data = _exchange_token(server, token)
    download_urls: dict[str, str] = token_data["download_urls"]

    dest = TEZ_DIR / tez_id
    context_dir = dest / "context"
    context_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"  Downloading Tez {tez_id} -> {dest}/")

    file_count = 0

    manifest_names = set(BUNDLE_FILES)
    for name, url in download_urls.items():
        if name in manifest_names:
            continue
        typer.echo(f"    - {name} ...", nl=False)
        try:
            dest_file = context_dir / name
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            _download_file(url=url, dest=dest_file)
            typer.echo(" done")
            file_count += 1
        except OSError as exc:
            typer.echo()
            typer.secho(f"  Download failed for {name}: {exc}", fg="red")
            raise typer.Exit(1) from exc

    for manifest_name in BUNDLE_FILES:
        manifest_url = download_urls.get(manifest_name)
        if not manifest_url:
            continue
        try:
            _download_file(url=manifest_url, dest=dest / manifest_name)
            file_count += 1
        except OSError as exc:
            typer.secho(f"  Download failed for {manifest_name}: {exc}", fg="red")
            raise typer.Exit(1) from exc

    typer.echo()
    typer.secho(f"  Done. {file_count} files -> {dest}/", bold=True)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size //= 1024
    return f"{size} TB"


def main() -> None:
    app()
