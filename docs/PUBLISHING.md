# Publishing to GitHub

Run these on your own machine (not from the assistant), where the repository
files are complete. Repo name: **hpe-networking-assistant**.

## Prerequisites

- A GitHub account.
- **Git installed.** Check with `git --version`. If it's "not recognized", install it:

  ```powershell
  winget install --id Git.Git -e
  winget install --id GitHub.cli -e   # optional; one-command repo creation
  ```

  (Or download from <https://git-scm.com/download/win> and run the installer with defaults.)
  **Then close PowerShell and open a new window** so `git` is on your PATH.

- First time using git on this machine, set your identity:

  ```powershell
  git config --global user.name "Your Name"
  git config --global user.email "you@example.com"
  ```

## 1. Open a terminal in the project folder

```
cd "C:\Users\67das\Claude\Projects\HPE Networking Extention\hpe-networking-assistant"
```

## 2. Initialize and make the first commit

```
git init -b main
git add .
git commit -m "HPE Networking Assistant v1.3.0"
```

## 3. Create the GitHub repo and push

**Option A — GitHub CLI (one command):**

```
gh repo create hpe-networking-assistant --public --source=. --remote=origin --push
```

**Option B — manual:** create an empty repo named `hpe-networking-assistant` at
<https://github.com/new> (do **not** add a README/license — this repo already has them), then:

```
git remote add origin https://github.com/<your-username>/hpe-networking-assistant.git
git push -u origin main
```

## 4. Cut the first release

The release workflow (`.github/workflows/release.yml`) builds the `.dxt` and
publishes a GitHub Release when you push a tag matching the manifest version.

```
git tag v1.3.0
git push origin v1.3.0
```

Watch the run under the repo's **Actions** tab. When it finishes, the
`hpe-networking-assistant-1.3.0.dxt` and `SHA256SUMS.txt` appear under
**Releases** — that's the file customers download and install.

## Notes

- The repo `homepage`/`documentation`/`support` URLs in `manifest.json` and the
  `README.md` links point to `github.com/hpe-networking-lab/hpe-networking-assistant`.
  If you publish under a different owner, update those to match.
- `.gitignore` already excludes secrets (`config.json`, `.env`,
  `.hpe-networking-assistant/`), build output, and caches.
- For future releases: bump the version in `manifest.json`, `pyproject.toml`, and
  `src/hpe_mist_mcp/__init__.py`, add a `docs/RELEASE_NOTES.md` entry, then push a
  matching `vX.Y.Z` tag. The release job fails the build if the tag and manifest
  version disagree.
