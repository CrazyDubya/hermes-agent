---
sidebar_position: 20
title: "Backup & Transfer to Another Machine"
description: "Back up your Hermes install and restore it on a new machine — config, API keys, skills, sessions, memory, and profiles."
---

# Backup & Transfer to Another Machine

Everything about your Hermes install — config, API keys, skills, memory, sessions, cron jobs, pairings — lives under a single directory: `~/.hermes/` (or whatever `HERMES_HOME` points at). Moving to a new machine is two commands:

```bash
# On the old machine
hermes backup

# On the new machine (after installing hermes)
hermes import hermes-backup-*.zip
```

That's the whole flow. The rest of this page covers what's actually in the zip, what's deliberately left out, and the gotchas when you restore.

## What's in a backup

`hermes backup` creates a zip of your entire `HERMES_HOME`, minus things that don't port cleanly. Concretely it includes:

- `config.yaml`, `.env`, `auth.json` — all your settings and credentials
- `state.db` — session metadata, tool-output history, memory, titles
- `skills/`, `plugins/`, `profiles/` — everything you've installed or customized
- `cron/jobs.json` — scheduled jobs
- `pairing/`, `platforms/pairing/` — approved users for messaging platforms
- `sessions/`, `logs/`, cached documents/images/audio, `gateway_state.json`, `channel_directory.json`, `processes.json`
- Per-platform state like `feishu_comment_pairing.json`

What's excluded (and why):

- **`hermes-agent/`** — the code itself. You reinstall `hermes` on the new machine; the repo isn't user data.
- **`checkpoints/`** — session-hash-keyed trajectory caches. They're tied to specific sessions and regenerated on demand; they wouldn't resolve to anything on the new machine.
- **`backups/`** — prior `hermes backup` zips. Don't nest backups exponentially.
- **`*.db-wal`, `*.db-shm`, `*.db-journal`** — SQLite sidecar files. The `*.db` itself gets a consistent snapshot via `sqlite3.backup()` (WAL-safe, works while Hermes is running). Shipping the live sidecars alongside would pair a fresh snapshot with stale transient state and produce a torn restore.
- **`__pycache__/`, `.git/`, `node_modules/`** — regeneratable or irrelevant.
- **`gateway.pid`, `cron.pid`** — runtime PID files, meaningless on a different host.

## Transferring to a new machine

### 1. On the old machine — create the backup

```bash
hermes backup
```

Output looks like:

```
Scanning ~/.hermes/ ...
Backing up 3142 files ...
  500/3142 files ...
  ...
Backup complete: /home/you/hermes-backup-2026-05-08-051630.zip
  Files:       3142
  Original:    412.7 MB
  Compressed:  187.3 MB
  Time:        8.4s

Restore with: hermes import hermes-backup-2026-05-08-051630.zip
```

Custom output path:

```bash
hermes backup -o /mnt/usb/hermes-move.zip
hermes backup -o /mnt/usb/               # directory → auto-names the file inside
```

### 2. Move the zip

scp, USB, Dropbox, whatever works. The zip contains credentials (`auth.json`, `.env`) — treat it like a password file. On restore, those files get `0600` permissions automatically.

### 3. On the new machine — install Hermes first

The backup doesn't include the codebase. Install Hermes normally before importing:

```bash
# See getting-started/installation for the full install flow
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/install.sh | bash
```

### 4. Import the backup

```bash
hermes import hermes-backup-2026-05-08-051630.zip
```

If `HERMES_HOME` on the new machine already has a `config.yaml` or `.env` (e.g., you ran `hermes setup` before importing), you'll get a confirmation prompt. Pass `--force` / `-f` to skip it:

```bash
hermes import hermes-backup-*.zip --force
```

Output:

```
Backup contains 3142 files
Target: ~/.hermes/
Importing 3142 files ...
Import complete: 3142 files restored in 4.1s
  Target: ~/.hermes/

  Profile aliases restored: work, personal

Done. Your Hermes configuration has been restored.
```

### 5. Verify and start

```bash
hermes doctor             # sanity check config + dependencies
hermes chat -q "hello"    # quick live test
```

If you ran gateways on the old machine, you'll need to re-enable them per profile on the new machine:

```bash
hermes gateway install
hermes -p work gateway install
```

`import` will remind you which profiles need this based on what it restored.

## Restore on the same machine

Same command — `hermes import` overlays the zip onto the current `HERMES_HOME`. Useful for rolling back after a bad config change or a corrupted session DB.

```bash
hermes import ~/hermes-backup-2026-05-01-120000.zip
```

## Quick snapshots (`--quick`)

For "just-in-case" pre-change snapshots — much smaller and faster than a full backup. Captures only critical state: `config.yaml`, `.env`, `auth.json`, `state.db`, `cron/jobs.json`, pairing stores, and a few platform-specific JSON blobs.

```bash
hermes backup --quick --label pre-upgrade
```

Snapshots are stored under `~/.hermes/state-snapshots/<timestamp>-<label>/` and auto-pruned to the last 20. These are NOT transferable zips — they're for local rollback. `hermes update` automatically takes one before pulling, so approved-user lists and pairing data are recoverable if anything goes sideways.

## Security notes

- **The backup zip contains plaintext credentials** (`.env`, `auth.json`). Store it like a password vault — encrypted disk, restricted share, or `gpg --symmetric` before upload.
- Restored secret files (`.env`, `auth.json`, `state.db`) get mode `0600` automatically.
- Path traversal in malicious zips is blocked on import — all extracted paths must resolve inside `HERMES_HOME`.

## What doesn't transfer cleanly

A few things in your install are machine-local and won't "just work" after import:

- **Gateway services** — systemd / launchd unit files live outside `HERMES_HOME`. Re-run `hermes gateway install` per profile on the new machine.
- **Absolute paths in config** — if you've set `terminal.workdir` or similar to an absolute path (e.g. `/home/old-user/projects`), fix those up for the new machine.
- **Docker containers / volumes** — if you use the Docker terminal backend, the container itself isn't in the backup. Re-pull the image.
- **Checkpoints** — intentional (see above). `/rollback` history doesn't port.
- **OS-specific integrations** — iMessage/BlueBubbles on macOS, Home Assistant local paths, etc. Re-test platform adapters.

## Troubleshooting

**"zip does not appear to be a Hermes backup"** — the validator looks for `config.yaml`, `.env`, or `state.db` somewhere in the archive. If you zipped a sub-directory or renamed the zip, unpack and re-zip from the `HERMES_HOME` root.

**Archive prefix detected** — if someone zipped the directory itself (creating `.hermes/config.yaml` entries instead of `config.yaml`), `hermes import` strips the `.hermes/` or `hermes/` prefix automatically. No action needed.

**Profile aliases not on PATH** — restored profiles create wrapper scripts in `~/.local/bin/`. If that's not in your PATH, `hermes import` prints the shell config snippet to add.

**"SQLite safe copy failed"** — extremely rare; usually means the source DB is locked by another process with an exclusive lock. The backup falls back to a raw copy and logs a warning. If the restored DB won't open, the backup captured a torn state — take a fresh one with Hermes idle.

## Related

- [`hermes backup` / `hermes import` reference](../reference/cli-commands.md#hermes-backup) — full flag list
- [Profiles](../user-guide/profiles.md) — multiple isolated installs, each backed up together
- [Updating & Uninstalling](../getting-started/updating.md) — `hermes update --backup` takes a pre-pull snapshot
