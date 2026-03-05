# Tez -- Data Flow Diagrams

All flows show the actors involved: **Claude** (orchestrator), **CLI** (local file I/O), **MCP** (server), **S3** (storage), **DynamoDB** (metadata).

The CLI exists only to augment what MCP cannot do -- file I/O and local config. Claude orchestrates all flows, calling MCP tools for data operations and CLI commands for local operations.

---

## 1. Login

```mermaid
sequenceDiagram
    participant C as Claude
    participant CLI as CLI

    C->>CLI: tez auth login --email adam@example.com --name "Adam Cross"
    CLI->>CLI: Write ~/.tez/config.json {email, name}
    CLI-->>C: "Logged in as Adam Cross (adam@example.com)"
```

> **Notes:** No server involvement. Identity is stored locally as a JSON file. No token exchange -- current implementation uses email-based trust. Name is required for share email notifications.

---

## 2. Whoami

```mermaid
sequenceDiagram
    participant C as Claude
    participant CLI as CLI

    C->>CLI: tez auth whoami
    CLI->>CLI: Read ~/.tez/config.json
    CLI-->>C: "Adam Cross (adam@example.com)"
```

> **Notes:** Pure local read. Exits with error if config file missing.

---

## 3. Build (Upload)

```mermaid
sequenceDiagram
    participant C as Claude
    participant MCP as MCP Server
    participant CLI as CLI
    participant S3 as S3
    participant DB as DynamoDB

    Note over C,DB: Phase 1 -- MCP reserves tez_id + generates upload URLs
    C->>MCP: tez_build(name, desc, creator, creator_name, files)
    MCP->>MCP: Generate unique tez_id (8-char hex)
    MCP->>DB: PutItem (status: pending_upload)
    DB-->>MCP: OK
    MCP->>S3: generate_presigned_url(PUT) per context file + manifest.json + tez.md
    S3-->>MCP: Pre-signed PUT URLs
    MCP->>MCP: Store URLs in TokenStore, return upload_token
    MCP-->>C: {tez_id, upload_token, server}

    Note over C,S3: Phase 2 -- CLI exchanges token + builds local bundle + uploads
    C->>CLI: tez build <tez_id> --name "X" --desc "Y" --server URL --token TOKEN file1 file2
    CLI->>MCP: GET /api/tokens/{token}
    MCP-->>CLI: {tez_id, upload_urls}
    CLI->>CLI: Copy files to /tmp/tez/{tez_id}/context/
    CLI->>CLI: Generate manifest.json + tez.md -> /tmp/tez/{tez_id}/
    Note over CLI: Local cache is now a complete protocol bundle
    loop Each context file + manifest.json + tez.md
        CLI->>S3: HTTP PUT (pre-signed URL + file bytes)
        S3-->>CLI: 200 OK
    end
    CLI-->>C: "Built and uploaded tez abc12345"

    Note over C,DB: Phase 3 -- MCP validates + activates
    C->>MCP: tez_build_confirm(tez_id)
    MCP->>S3: HeadObject per file + manifest.json + tez.md
    S3-->>MCP: 200 + ETag
    MCP->>DB: UpdateItem (status: active)
    DB-->>MCP: OK
    MCP-->>C: {tez_id, status: "active"}
```

> **Notes:** Claude orchestrates the 3-phase flow across MCP and CLI. MCP owns all AWS operations (DynamoDB writes, pre-signed URL generation). The CLI exchanges a short-lived token for pre-signed URLs via `GET /api/tokens/{token}`, then builds the protocol bundle locally -- the creator has a usable local copy at `/tmp/tez/{tez_id}/` before anything hits S3. Then uploads via pre-signed URLs. Zero AWS credentials in the CLI.

---

## 4. Share

```mermaid
sequenceDiagram
    participant C as Claude
    participant MCP as MCP Server
    participant DB as DynamoDB
    participant SG as SendGrid

    C->>MCP: tez_share(tez_id, recipient_email, caller)
    MCP->>DB: GetItem(tez_id)
    DB-->>MCP: Record (verify caller is creator)
    MCP->>DB: UpdateItem -- append to recipients[]
    DB-->>MCP: OK

    alt SendGrid API key configured
        MCP->>SG: POST /mail/send (share notification)
        SG-->>MCP: 202 Accepted
    end

    MCP-->>C: {tez_id, shared_with, email_sent}
```

> **Notes:** MCP-only flow -- no CLI involvement. Updates DynamoDB recipients first, then optionally sends email. The recipient list is the source of truth for access control -- email is just a notification. Uses `creator_name` from the record for the email ("Adam shared this with you").

---

## 5. Download

```mermaid
sequenceDiagram
    participant C as Claude
    participant MCP as MCP Server
    participant CLI as CLI
    participant S3 as S3
    participant DB as DynamoDB

    Note over C,DB: Phase 1 -- MCP authorises + generates download URLs
    C->>MCP: tez_download(tez_id, caller)
    MCP->>DB: GetItem(tez_id)
    DB-->>MCP: Record
    MCP->>MCP: Verify caller is creator or in recipients[]
    MCP->>S3: generate_presigned_url(GET) per context file + manifest.json + tez.md
    S3-->>MCP: Pre-signed GET URLs
    MCP->>MCP: Store URLs in TokenStore, return download_token
    MCP-->>C: {tez_id, name, files, download_token, server}

    Note over C,S3: Phase 2 -- CLI exchanges token + downloads to local cache
    C->>CLI: tez download <tez_id> --server URL --token TOKEN
    CLI->>MCP: GET /api/tokens/{token}
    MCP-->>CLI: {tez_id, download_urls}
    loop Each context file
        CLI->>S3: HTTP GET (pre-signed URL)
        S3-->>CLI: File bytes
        CLI->>CLI: Write to /tmp/tez/{tez_id}/context/
    end
    CLI->>S3: HTTP GET manifest.json + tez.md
    S3-->>CLI: File bytes
    CLI->>CLI: Write to /tmp/tez/{tez_id}/
    Note over CLI: Local cache is now a complete protocol bundle
    CLI-->>C: "Done. N files -> /tmp/tez/{tez_id}/"
```

> **Notes:** MCP handles authorisation and URL generation. The CLI exchanges a short-lived token for pre-signed URLs via `GET /api/tokens/{token}`, then downloads to `/tmp/tez/{tez_id}/` producing an identical local bundle to what `build` creates -- same protocol structure, same paths. Zero AWS credentials. Download URLs expire after 60 minutes.

---

## 6. List

```mermaid
sequenceDiagram
    participant C as Claude
    participant MCP as MCP Server
    participant DB as DynamoDB

    C->>MCP: tez_list(caller)
    MCP->>DB: Query creator-index GSI (PK=caller)
    DB-->>MCP: Created Tez records
    MCP->>DB: Scan with filter (recipients contains caller)
    DB-->>MCP: Shared Tez records
    MCP-->>C: {created: [...], shared_with_me: [...]}
```

> **Notes:** MCP-only flow. Uses a GSI (`creator-index`) for efficient "my Tez" queries. "Shared with me" uses a Scan with filter -- acceptable at scale, would benefit from a GSI at higher scale.

---

## 7. Info

```mermaid
sequenceDiagram
    participant C as Claude
    participant MCP as MCP Server
    participant DB as DynamoDB

    C->>MCP: tez_info(tez_id, caller)
    MCP->>DB: GetItem(tez_id)
    DB-->>MCP: Record
    MCP->>MCP: Verify caller is creator or in recipients[]
    MCP-->>C: {tez_id, name, creator, description, status, files, recipients}
```

> **Notes:** MCP-only flow. Returns metadata only -- no pre-signed URLs, no S3 interaction. Lighter weight than download for when Claude just needs to understand what a Tez contains.

---

## 8. Delete

```mermaid
sequenceDiagram
    participant C as Claude
    participant MCP as MCP Server
    participant S3 as S3
    participant DB as DynamoDB

    C->>MCP: tez_delete(tez_id, caller)
    MCP->>DB: GetItem(tez_id)
    DB-->>MCP: Record (verify caller is creator)
    MCP->>S3: ListObjectsV2(prefix: tez_id/)
    S3-->>MCP: Object keys
    MCP->>S3: DeleteObjects(keys)
    S3-->>MCP: OK
    MCP->>DB: DeleteItem(tez_id)
    DB-->>MCP: OK
    MCP-->>C: {tez_id, status: deleted}
```

```mermaid
sequenceDiagram
    participant C as Claude
    participant CLI as CLI

    Note over C,CLI: Local cache clean (no server)
    C->>CLI: tez cache clean <tez_id>
    CLI->>CLI: rm -rf /tmp/tez/<tez_id>/
    CLI-->>C: "Removed /tmp/tez/<tez_id>/"
```

> **Notes:** Two levels of delete. `tez_delete` (MCP) permanently removes the Tez from S3 and DynamoDB -- creator only. `tez cache clean` (CLI) just removes locally downloaded files -- no auth needed.

---

## 9. Logout

```mermaid
sequenceDiagram
    participant C as Claude
    participant CLI as CLI

    C->>CLI: tez auth logout
    CLI->>CLI: Delete ~/.tez/config.json
    CLI-->>C: "Logged out."
```

> **Notes:** Pure local operation. Removes the config file. No server-side session to invalidate.

---

## Responsibility Summary

| Actor | Responsibilities |
|-------|-----------------|
| **Claude** | Orchestrates all flows. Calls MCP tools first (for IDs, URLs, metadata), then CLI for file I/O. |
| **CLI** | Local file I/O only -- build local protocol bundles, upload/download via pre-signed URLs, local auth (`~/.tez/config.json`), cache cleanup. Zero AWS credentials. |
| **MCP Server** | All AWS operations -- metadata CRUD, authorisation checks, pre-signed URL generation (upload + download), email notifications, S3 delete. Never touches file bytes. |
| **S3** | File storage (one folder per Tez). Serves uploads/downloads via pre-signed URLs. Versioning enabled. |
| **DynamoDB** | Metadata store -- ownership, recipients, status, file lists. GSI for creator queries. Source of truth for access control. |
