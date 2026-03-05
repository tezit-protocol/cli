# Tez CLI

Reference CLI for the [Tezit Protocol](https://github.com/tezit-protocol/spec) -- local file I/O, protocol bundle assembly, and pre-signed URL transfers.

## What it does

The CLI handles all local operations for Tez lifecycle management:

- **Build** -- assemble protocol bundles from local files, upload via pre-signed URLs
- **Download** -- fetch bundles via pre-signed URLs, assemble into local cache
- **Auth** -- manage local identity
- **Cache** -- clean up locally cached files

The CLI holds zero cloud credentials. All storage URLs are obtained by exchanging short-lived tokens with the [Tez MCP Server](https://github.com/tezit-protocol/mcp-server).

## Architecture

```
LLM Orchestrator
    |
    +-- MCP Tools --> MCP Server (tezit-protocol/mcp-server)
    |
    +-- Shell -----> CLI (this repo)
                         +-- Local file I/O
                         +-- Protocol bundle assembly
                         +-- Upload via pre-signed URLs
                         +-- Download via pre-signed URLs
```

See [Proposal #8](https://github.com/tezit-protocol/spec/issues/8) for the full architecture description.

## Usage

```bash
# Authenticate
tez auth login

# Build and upload a Tez
tez build <tez_id> --token TOKEN --server URL file1.md file2.pdf

# Download a Tez
tez download <tez_id> --token TOKEN --server URL

# Clean local cache
tez cache clean <tez_id>
```

## Development

```bash
# Install dependencies
uv sync --dev

# Run tests
uv run pytest --cov --cov-report=term-missing

# Lint
uv run ruff check .
uv run ruff format --check .

# Type check
uv run mypy src/
```

## Related

- [Tezit Protocol Spec](https://github.com/tezit-protocol/spec) -- protocol specification
- [Tez MCP Server](https://github.com/tezit-protocol/mcp-server) -- companion MCP server for metadata and storage
