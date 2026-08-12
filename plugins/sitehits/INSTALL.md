# SiteHits plugin installation

SiteHits uses Streamable HTTP at `https://sitehits.io/mcp` and OAuth discovery. The canonical OAuth resource is the same URL.

## Codex plugin setup

Installing the plugin registers the `sitehits` MCP server from `.mcp.json`. Authenticate it with the native OAuth command:

```bash
codex mcp login --scopes read,write sitehits
```

The consent page uses the user's SiteHits account. A successful login does not grant access beyond that user's existing SiteHits resources.
MCP tools never return private bot or product keys; tracking setup uses environment variable names
and redacted placeholders. Obtain real values only through an authorized SiteHits UI or secret
manager workflow.

## Additive standalone setup

Inspect an existing entry before changing it:

```bash
codex mcp get sitehits
```

If no `sitehits` entry exists, add it without changing other MCP servers:

```bash
codex mcp add sitehits \
  --url https://sitehits.io/mcp \
  --oauth-resource https://sitehits.io/mcp
codex mcp login --scopes read,write sitehits
```

Keep an existing entry when it already uses the canonical URL. If the name exists with another URL, do not overwrite it silently; resolve the naming or environment conflict first.

## CLI fallback

Confirm that the CLI on `PATH` supports MCP before setup:

```bash
codex mcp --help
```

On macOS, an older standalone CLI may not have the `mcp` command. In that case, use the CLI bundled with the Codex desktop app for every setup command:

```bash
/Applications/ChatGPT.app/Contents/Resources/codex mcp --help
/Applications/ChatGPT.app/Contents/Resources/codex mcp login --scopes read,write sitehits
```

If no compatible CLI is available, use the client's native **Settings → MCP → Authenticate** flow.

## Verify without a mandatory restart

After authentication, call one safe read-only tool such as `list_sites`. If it succeeds, no restart is needed. If the server is present but authentication fails, repeat OAuth login instead of restarting. If the tool is absent, use the client's native reload once and retry in the current task; open a new task only if the client still has not loaded the plugin.

## Updates

Use the plugin's normal update mechanism for plugin installations. Do not let the bundled skill update itself. A separately copied skill is a snapshot; update it only on an explicit user request and use the single `skill_update_url` published by `https://sitehits.io/agent-manifest.json` or returned by `get_integration_status`.

During setup, an explicit update, or connection diagnostics, compare the local `skills/sitehits-analytics/VERSION` value with `get_integration_status`. Do not perform this check during normal analytics or CRUD requests. After an update, try a read-only tool in the current task before considering reload or restart.
