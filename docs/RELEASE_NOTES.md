# Release Notes

All notable changes to the HPE Networking Assistant are documented here.
This project adheres to [Semantic Versioning](https://semver.org).

## [1.3.0] — Status & write-readiness wizard

### Added
- **`get_status` tool** — reports the current mode (read-only vs read-write), region, organization, account, and whether the token can actually make changes. Answers "am I read-only or read-write?" in chat.
- **Write-readiness detection** — when write mode is enabled, validation and onboarding check the token's Mist role. A read-only token is reported clearly with step-by-step guidance to create a write-capable token (under an account with a write role such as Network Admin) and update it in the extension Settings.
- `start_setup` now includes a `write_access` section in its report.

### Notes
- A Mist API token **cannot be elevated** to read-write via the API — it inherits the role of the account that created it. The guided flow therefore detects and directs; the customer supplies a write-capable token through the secure Settings form (stored in the OS keychain), never through chat.
- Enabling write mode in Settings takes effect when the extension restarts.

## [1.2.0] — First-run onboarding wizard

**Token is now the only thing a customer enters.** The static region/org form fields are gone; everything is auto-discovered.

### Added
- **`start_setup` onboarding tool** that runs the full first-run wizard: detects the region, discovers organizations and sites, runs validation, and returns a **READY FOR USE** report. Surfaced to Claude via server `instructions` so it runs on first use.
- **Automatic region detection** (`discovery.py`): probes all twelve Mist clouds in parallel with the token and keeps the one that authenticates — no API endpoint required.
- Organizations are chosen **by name**; when several are accessible, the wizard asks the user to pick by name and resolves the id internally.
- Discovered region/organization are persisted (without the token) so they are never probed or re-entered again.

### Changed
- The Claude Desktop install form now collects only the **API token** (plus the optional write toggle). Region and Org ID fields were removed.
- The CLI setup wizard (`hpe-mist-setup`) auto-detects the region instead of prompting for it; `MIST_REGION` still works as an override.
- Customers never need to know their **Org ID, Site ID, or API endpoint**.

## [1.1.0] — Optional write mode

**Adds opt-in write capabilities.** Read-only remains the default.

### Added
- **Write mode** (off by default), enabled via the extension's *Enable write operations* setting or the `MIST_WRITE_ENABLED` environment variable. When enabled, five write tools are registered:
  - `rename_device` — rename an AP or switch
  - `reboot_device` — reboot a device
  - `locate_device` — toggle a device's locate LED
  - `claim_devices` — claim devices into an org by activation code
  - `assign_devices_to_site` — assign inventory devices to a site
- Every write tool requires `confirm: true`; without it, no API call is made.
- Mist client gained method/body support and a `read_only` guard that blocks non-GET requests unless write mode is on. Writes are never auto-retried.
- Setup wizard prompts for write mode (defaults to read-only); validation report shows the active operating mode.

### Security
- Three independent gates protect writes: the mode toggle, the token's own privileges, and per-call confirmation. Write tools are not advertised to Claude when the mode is off.

## [1.0.0] — Phase 1 MVP

**First public release.** A read-only Juniper Mist assistant packaged as a Claude Desktop extension.

### Added
- Python MCP server (`hpe_mist_mcp`) with six read-only tools:
  - `get_organizations` — discover accessible organizations
  - `get_sites` — list sites in an organization
  - `get_access_points` — AP inventory (org-wide or per site)
  - `get_switches` — switch inventory (org-wide or per site)
  - `get_clients` — currently connected wireless clients
  - `get_offline_access_points` — offline AP report
- Dependency-free Mist API client supporting all twelve global cloud regions (Global, EMEA, APAC), with token auth, retries, rate-limit handling, and pagination.
- Configurable region selection via region code, API host, or portal host.
- Claude Desktop extension manifest (`manifest.json`) with secure token storage (OS keychain) and upgrade support.
- Interactive **Setup Wizard** (`hpe-mist-setup`): token → validate → discover orgs → discover sites → save config → validation tests.
- **Validation Framework** (`hpe-mist-validate`) producing a **READY FOR USE** / **REQUIRES ATTENTION** verdict across authentication, organization, site, and inventory checks.
- Documentation: README, Installation Guide, Customer Onboarding Guide.
- Test suite with mocked Mist API (no token or network required).
- GitHub Actions workflows for CI and release packaging.

### Security
- Strictly read-only; no tools mutate Mist configuration.
- API token is masked in diagnostics and never written to logs.

## [Unreleased] — Phase 2 (planned)

- Access Assurance insights
- Authentication troubleshooting workflows
- Client trace workflows
- NAC Visualizer integration
- Report generation
