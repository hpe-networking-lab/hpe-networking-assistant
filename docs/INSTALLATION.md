# Installation Guide

From zero to *"Show all APs in my organization"* in under ten minutes. All you need is an API token — the assistant discovers everything else (region, organizations, sites) for you.

## Prerequisites

- **Claude Desktop** (latest version) — <https://claude.ai/download>
- **Python 3.10 or newer** on your machine (the extension runs a Python MCP server). Verify with `python --version`.
- A **Juniper Mist account** with at least read access to your organization.

---

## Step 1 — Create a Mist API token

1. Sign in to the Mist portal (e.g. `https://manage.mist.com`).
2. Click your account name (top-right) → **My Account**.
3. Scroll to **API Token** → **Create Token**.
4. Copy the token immediately and store it somewhere safe — **it is shown only once.**

The token inherits your account permissions. Because the assistant is read-only by default, an **Observer / Read-only** role is sufficient and recommended. (You only need a role with write privileges if you intend to enable write mode.)

You do **not** need to know your region, API endpoint, Org ID, or Site ID. The assistant figures those out from the token.

---

## Step 2 — Install the extension

1. Download `hpe-networking-assistant.dxt` from the [latest GitHub release](https://github.com/hpe-networking/hpe-networking-assistant/releases/latest).
2. Open Claude Desktop → **Settings → Extensions**.
3. Drag the `.dxt` file into the window (or double-click it in your file manager).
4. Claude Desktop shows a short form. Enter:
   - **Mist API Token** — paste the token from Step 1 (stored in your OS keychain). *This is the only required field.*
   - **Enable write operations** — leave **OFF** for safe read-only use (see [About write mode](#about-write-mode)).
5. Click **Install / Enable**.

---

## Step 3 — Run the onboarding wizard

Open a new Claude Desktop chat and say:

> Set me up.

Claude runs the `start_setup` tool, which automatically:

1. Detects your Mist region from the token (no API endpoint needed).
2. Discovers the organizations your token can access.
3. Discovers your sites.
4. Runs validation tests (authentication, org, site, and inventory access).
5. Returns a **READY FOR USE** report.

If your token can reach more than one organization, Claude will list them **by name** and ask which to use — just answer with the name. You never type an Org ID.

Once you see **READY FOR USE**, try:

> Show all APs in my organization.
> Which access points are offline right now?
> How many clients are connected at the HQ site?

---

## About write mode

Write mode is off by default — the assistant can only read from Mist. If you turn it on (in the extension settings), it gains tools to rename and reboot devices, toggle locate LEDs, and claim/assign inventory. Even then, every write requires Claude to pass an explicit confirmation, and your API token must have write privileges in Mist.

### Enabling write mode and getting a write-capable token

1. In Claude Desktop → **Settings → Extensions → HPE Networking Assistant**, turn on **Enable write operations**. The change takes effect when the extension restarts (Claude Desktop handles this).
2. In a chat, ask: **"Check my setup."** Claude runs `get_status` and reports whether your token can actually make changes.
3. If it reports your token is **read-only**, follow the guidance it returns. A Mist API token keeps the role of the account that created it and **cannot be upgraded via the API**, so you need a token from an account with a write role:
   - In Mist, ensure you have (or ask an admin for) a role such as **Network Admin / Super User**.
   - Create a new token under that account: **My Account → API Token → Create Token**.
   - Back in the extension Settings, **replace the API Token** with the new one (keep *Enable write operations* on). The token is stored in your OS keychain.
4. Ask **"Check my setup"** again — you should now see write access confirmed.

To return to read-only, just turn the toggle off.

---

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| Setup reports "Could not validate this token against any Mist cloud" | Bad/expired token, or no network to Mist | Re-create the token in the Mist portal and run "set me up" again. |
| "REQUIRES ATTENTION" on org or inventory checks | Token role lacks visibility | Confirm the token's role grants read access to the org. |
| "You have access to multiple organizations…" | Token spans several orgs | Tell Claude which organization to use **by name**. |
| Extension fails to start | Python not found / older than 3.10 | Install Python 3.10+ and ensure `python` is on your PATH. |

For a structured health check, developers can run `hpe-mist-validate`, which prints **READY FOR USE** or **REQUIRES ATTENTION** with per-check detail.

### Region reference (optional)

You never need this — region detection is automatic — but for reference, the API endpoint mirrors your portal URL with `manage` replaced by `api`:

| Portal host | Region code | API endpoint |
| --- | --- | --- |
| `manage.mist.com` | `global01` | `api.mist.com` |
| `manage.gc1.mist.com` | `global02` | `api.gc1.mist.com` |
| `manage.ac2.mist.com` | `global03` | `api.ac2.mist.com` |
| `manage.gc2.mist.com` | `global04` | `api.gc2.mist.com` |
| `manage.gc4.mist.com` | `global05` | `api.gc4.mist.com` |
| `manage.eu.mist.com` | `emea01` | `api.eu.mist.com` |
| `manage.gc3.mist.com` | `emea02` | `api.gc3.mist.com` |
| `manage.ac6.mist.com` | `emea03` | `api.ac6.mist.com` |
| `manage.gc6.mist.com` | `emea04` | `api.gc6.mist.com` |
| `manage.ac5.mist.com` | `apac01` | `api.ac5.mist.com` |
| `manage.gc5.mist.com` | `apac02` | `api.gc5.mist.com` |
| `manage.gc7.mist.com` | `apac03` | `api.gc7.mist.com` |

To force a specific region (rare), advanced users running the CLI can set `MIST_REGION`.

---

## Upgrading

When a new release is published, download the new `.dxt` and install it the same way; Claude Desktop replaces the previous version and preserves your stored token and discovered configuration. See [RELEASE_NOTES.md](RELEASE_NOTES.md) for version history.
