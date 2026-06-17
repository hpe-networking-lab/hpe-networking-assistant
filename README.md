# HPE Networking Assistant

[![Release](https://img.shields.io/github/v/release/hpe-networking-lab/hpe-networking-assistant?sort=semver)](https://github.com/hpe-networking-lab/hpe-networking-assistant/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/hpe-networking-lab/hpe-networking-assistant/ci.yml?branch=main&label=CI)](https://github.com/hpe-networking-lab/hpe-networking-assistant/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

Query your **Juniper Mist** network in natural language, directly from Claude Desktop.

**➡ [Download the latest `.dxt`](https://github.com/hpe-networking-lab/hpe-networking-assistant/releases/latest)** and install it in Claude Desktop (Settings → Extensions).

The HPE Networking Assistant is a read-only [Model Context Protocol (MCP)](https://modelcontextprotocol.io) server packaged as a one-click Claude Desktop extension. Network engineers can ask Claude things like *"Show all APs in my organization"* or *"Which access points are offline right now?"* and get answers pulled live from the Mist API — no manual API calls, no scripting.

> **Read-only by default.** Out of the box the assistant only reads from Mist. An opt-in **write mode** (off by default) adds tools that can change your environment — see [Write mode](#write-mode-opt-in).

---

## What it can do

| Capability | Tool |
| --- | --- |
| First-run onboarding: detect region, discover orgs/sites, validate, report READY FOR USE | `start_setup` |
| Report mode (read-only/read-write), region, org, and write readiness | `get_status` |
| Discover the organizations your token can access | `get_organizations` |
| List sites in an organization | `get_sites` |
| Inventory access points (org-wide or per site) | `get_access_points` |
| Inventory switches (org-wide or per site) | `get_switches` |
| List currently connected wireless clients | `get_clients` |
| List wired clients on switch ports (switch, port, VLAN, IP) | `get_wired_clients` |
| Report offline access points | `get_offline_access_points` |
| What's wrong? — Marvis AI suggested actions + fixes | `get_marvis_actions` |
| List organization alarms (severity/type counts) | `get_alarms` |
| Per-site Service Level Expectations (experience scores) | `get_sle` |
| Switch/device port stats (link, PoE, neighbor, traffic) | `get_switch_ports` |
| Back up the org configuration to JSON (read-only) | `export_org_config` |
| Set the session's default org by name (multi-org tokens) | `set_active_org` |
| Compare current config to a saved backup (config drift) | `diff_org_config` |
| Generate a network health report (Markdown) | `generate_health_report` |
| Generate a full device inventory report (Markdown) | `generate_inventory_report` |
| Firmware compliance report (drift vs fleet target) | `generate_firmware_report` |
| Locate a wireless client by MAC or hostname | `find_client` |
| Trace a client's connection events to troubleshoot | `trace_client` |
| List Access Assurance (NAC) authenticated clients | `get_nac_clients` |
| Troubleshoot 802.1X/MAB authentication by MAC **or** username/cert CN (NAC events) | `troubleshoot_authentication` |
| Build an HTML Access Assurance (NAC) dashboard | `generate_nac_dashboard` |

All twelve Mist global cloud regions are supported (Global, EMEA, APAC), auto-detected from your token.

### Companion live dashboards (Cowork)

In addition to the packaged extension tools, two live, connector-backed dashboards are available in Claude Cowork (separate from the `.dxt`):

- **Mist Network & Access Assurance dashboard** — device health and NAC charts that auto-refresh every 30s.
- **NAC Auth Debugger** — type a username or MAC to isolate that identity's authentication events, decode the most recent failure, and hand the detail to Claude for a fix suggestion.

### Write mode (opt-in)

Write mode is **disabled by default**. When you enable it in the extension settings, these additional tools become available — and **each one requires `confirm: true` before it does anything**:

| Capability | Tool |
| --- | --- |
| Rename an AP or switch | `rename_device` |
| Reboot a device | `reboot_device` |
| Toggle a device's locate LED | `locate_device` |
| Claim devices into an org | `claim_devices` |
| Assign devices to a site | `assign_devices_to_site` |

Three layers of protection apply: write mode must be explicitly enabled, your API token must itself have write privileges, and every write call must pass `confirm: true`. When write mode is off, the write tools are not even advertised to Claude.

**Enabling write mode.** Toggle **Enable write operations** in the extension Settings (Claude Desktop → Settings → Extensions → HPE Networking Assistant); the change takes effect when the extension restarts. Then ask Claude to *"check my setup"* (`get_status`). Because a Mist token inherits the role of the account that created it and **cannot be elevated via the API**, if your current token is read-only the status check will say so and walk you through creating a write-capable token (under an account with a Network Admin role) and pasting it back into Settings — the token stays in your OS keychain.

---

## Quick start

1. Install [Claude Desktop](https://claude.ai/download).
2. Download `hpe-networking-assistant.dxt` from the [latest release](https://github.com/hpe-networking-lab/hpe-networking-assistant/releases/latest).
3. Double-click the file (or drag it into Claude Desktop → **Settings → Extensions**).
4. When prompted, paste your **Mist API token** — that's the only field.
5. In a new chat, say **"Set me up."** The assistant auto-detects your region, discovers your organizations and sites, runs validation, and reports **READY FOR USE**.
6. Ask Claude: **"Show all APs in my organization."**

You never need to know your Org ID, Site ID, or API endpoint. The whole process takes under ten minutes. See the [Installation Guide](docs/INSTALLATION.md) for details and the [Onboarding Guide](docs/ONBOARDING.md) for example prompts.

---

## Getting a Mist API token

In the Mist portal: **My Account → API Token → Create Token**. Copy the token immediately — it is shown only once. The token inherits your account's permissions; for this read-only assistant an Observer/Read role is sufficient. See the [Installation Guide](docs/INSTALLATION.md#step-1-create-a-mist-api-token) for screenshots and tips.

---

## Region detection

You don't pick a region. On first run, `start_setup` probes the Mist clouds with your token and keeps the one that authenticates, so the correct API endpoint is found automatically. The detected region is saved so it isn't probed again. The full list of twelve supported regions is in [`src/hpe_mist_mcp/regions.py`](src/hpe_mist_mcp/regions.py). Advanced users running the CLI can override detection with the `MIST_REGION` environment variable.

---

## Developing locally

```bash
git clone https://github.com/hpe-networking-lab/hpe-networking-assistant.git
cd hpe-networking-assistant
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Configure + validate against your Mist tenant
hpe-mist-setup                 # interactive setup wizard
hpe-mist-validate              # prints READY FOR USE / REQUIRES ATTENTION

# Run the MCP server over stdio (for manual testing)
hpe-mist-mcp
```

Run the test suite (no network or token required — the Mist API is mocked):

```bash
pytest
```

---

## How it works

```
Claude Desktop  ⇄  MCP (stdio)  ⇄  hpe_mist_mcp.server  ⇄  Mist REST API (HTTPS, GET only)
```

- `mist_client.py` — dependency-free Mist API client (standard library only): inventory, clients, search, NAC, and guarded writes.
- `server.py` — MCP server implementing the JSON-RPC stdio transport with the standard library only (no third-party packages), exposing the read-only tools plus the opt-in write tools.
- `config.py` — resolves the token/region/org from environment variables (injected by the extension) or a local config file (written by the setup wizard).
- `discovery.py` — automatic Mist region detection from the token.
- `setup_wizard.py` / `validation.py` — guided onboarding and a health check that emits **READY FOR USE** or **REQUIRES ATTENTION**.
- `reports.py` / `nac_visualizer.py` — Markdown reports and the self-contained HTML NAC dashboard.

The packaged extension stores your API token in the OS keychain (macOS Keychain / Windows Credential Manager) and passes it to the server via the `MIST_API_TOKEN` environment variable.

---

## Roadmap

**Phase 2 is complete.** Report generation (v1.4.0), client trace/troubleshooting (v1.5.0), Access Assurance & auth troubleshooting (v1.6.0), the NAC Visualizer HTML dashboard (v1.7.0), and per-user auth debugging (v1.8.0). Full history in [`docs/RELEASE_NOTES.md`](docs/RELEASE_NOTES.md).

---

## Security

- Read-only by default: no write tools are exposed unless write mode is explicitly enabled.
- Defense in depth for writes: write mode must be enabled, the token must have write privileges, and each write requires `confirm: true`. Writes are never auto-retried.
- The API token is never logged (it is masked in any diagnostic output).
- Tokens are stored in the OS keychain by the extension, or in a `0600`-permissioned file by the setup wizard.

Report security issues via the [issue tracker](https://github.com/hpe-networking-lab/hpe-networking-assistant/issues).

---

## License

MIT — see [LICENSE](LICENSE).
