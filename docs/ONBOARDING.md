# Customer Onboarding Guide

Welcome to the **HPE Networking Assistant**. This guide helps a new network engineer get productive quickly after installation.

## 1. What this assistant is (and isn't)

It is a natural-language window into your Juniper Mist environment. You ask questions in plain English; Claude translates them into Mist API calls and summarizes the results.

In this release it is **read-only**. It will never rename, reconfigure, reboot, or delete anything in Mist. You can use it confidently in production without risk of accidental changes.

## 2. First five minutes

After installing the extension (see the [Installation Guide](INSTALLATION.md)) and pasting your API token, open a new chat and say:

> Set me up.

The assistant runs onboarding automatically — it detects your Mist region, discovers your organizations and sites, runs validation tests, and reports **READY FOR USE**. If you have access to more than one organization, it will list them by name and ask which to use; just answer with the name.

Once you see READY FOR USE, confirm with:

> List the sites in my organization.

You never need to enter or look up a region, Org ID, Site ID, or API endpoint.

## 3. Things to ask

**Inventory**
- "Show all APs in my organization."
- "List the switches at the San Jose site."
- "How many access points do we have in total?"

**Health**
- "Which access points are offline right now?"
- "Give me a count of online vs offline APs."

**Clients**
- "How many wireless clients are connected at HQ?"
- "List clients on the Corp SSID at the Austin site."

**Discovery**
- "What sites do we have and where are they?"
- "What's the org ID for Acme?"

## 3a. Phase 2 — reports, troubleshooting, and dashboards

**Reports** (Claude returns Markdown you can save as `.md` or convert to PDF/Word)
- "Generate a network health report for my organization."
- "Create an inventory report of all my APs and switches and save it as a file."

**Client trace / troubleshooting**
- "Find the client with MAC aa:bb:cc:dd:ee:ff." / "Where is hostname *Davids-Laptop*?"
- "Trace client aa:bb:cc:dd:ee:ff over the last hour — why does it keep dropping?"
- "Show this client's connection events for the past day."

**Access Assurance (NAC)**
- "List the NAC clients authenticated in the last day."
- "Show only EAP-TLS NAC clients." / "Show wired MAB clients."
- "Troubleshoot authentication failures in the last 24 hours."
- "Why is user bob@corp failing 802.1X? Trace their auth events." (pass the MAC)

**NAC dashboard**
- "Build a NAC dashboard for my org and save it as an HTML file I can open."

Tips: most tools accept a `duration` like `1h`, `1d`, or `7d`. For client/auth
troubleshooting, give Claude the MAC address when you have it — it focuses the
results. Report and dashboard tools return Markdown/HTML; just ask Claude to
"save that as a file" and it will drop it in your folder.

## 4. Working across multiple organizations

If your token can access more than one organization, either:

- set a **Default Organization ID** in the extension settings, or
- name the organization in your prompt: *"Show APs in the Acme org (org-id 1234…)."*

Ask *"What organizations can I access?"* to get the exact IDs.

## 5. Understanding results

- **Offline AP** = an access point whose Mist inventory `connected` flag is `false`. This typically means it is powered off, unplugged, or has lost its uplink.
- **Client lists** reflect *currently connected* wireless clients at query time, not historical sessions.
- Large environments are paginated automatically; counts reflect the full result set.

## 6. Tips for good answers

- Be specific about scope (org vs site) when you can — it's faster and unambiguous.
- Use site names from `get_sites`; Claude can map names to IDs for you.
- If a result seems incomplete, ask Claude to "list all of them" — it will page through everything.

## 7. Getting help

- Re-run the connectivity checks above.
- Developers/admins can run `hpe-mist-validate` for a **READY FOR USE / REQUIRES ATTENTION** report.
- File issues at <https://github.com/hpe-networking-lab/hpe-networking-assistant/issues>.

## 8. What's next (Phase 2)

Planned additions include Access Assurance insights, authentication troubleshooting, client trace workflows, NAC Visualizer integration, and report generation. See [RELEASE_NOTES.md](RELEASE_NOTES.md).
