# qs-steam

**QuickShell Steam** — automatically surface your installed Steam **and Lutris** games in a QuickShell application launcher, including game icons and artwork.

Initially built for **Ryoku Shell** on **CachyOS + Hyprland**, but the generated [freedesktop.org desktop entries](https://specifications.freedesktop.org/desktop-entry-spec/latest/) can also be used by other app launchers that index XDG applications.

> This is an independent community utility. Not affiliated with Valve, Steam, Lutris, or Ryoku Shell.

## What it does

- Finds **installed** Steam games, including games in additional Steam library folders (even paths containing spaces).
- Supports native Linux Steam as well as Steam installed via Flatpak.
- Finds installed Lutris games from Lutris' local database; supports native and Flatpak Lutris, and skips Steam-runner duplicates.
- Uses per-game Steam icons from local icon and library caches (including newer hashed filenames), user grid art where applicable, and existing Lutris icons/covers.
- Falls back to public Steam artwork when a game has no matching local image (no API key needed; at most six new requests per run, cached locally).
- Maintains `.desktop` shortcuts under `~/.local/share/applications/` so the games appear in Ryoku's launcher.
- Adds a **systemd user timer** that refreshes the library after login and every five minutes.
- Removes only obsolete `ryoku-game-*.desktop` shortcuts managed by this utility.

**Launch behavior:** Shortcuts delegate to `steam steam://rungameid/APPID` or `lutris lutris:rungameid/ID`. Your existing Steam/Lutris runners, launch options, and compatibility choices remain managed by those applications.

## Requirements

- Linux desktop that reads XDG `.desktop` applications (designed for Ryoku Shell / QuickShell).
- Python **3.10+** (standard library only).
- A working native or Flatpak Steam installation and/or Lutris installation.
- `systemd --user` for automatic background sync. Manual runs do not require a timer.

No `sudo` and no pip installation are required.

## Install

```bash
git clone https://github.com/zarzorr69/qs-steam.git
cd qs-steam
python3 qs-steam.py --install
```

This copies the synchronizer to `~/.local/bin/ryoku-game-sync`, enables `ryoku-game-sync.timer` as a user service, and immediately scans your installed games.

**Upgrading from the original `ryoku-game-sync.py` or `ryoku-game-sync-v2.py`:** Just run `python3 qs-steam.py --install`. It replaces the same installed command, timer, and managed shortcut files, retaining compatibility with your existing setup.

## Commands

```bash
# Run the sync now
~/.local/bin/ryoku-game-sync

# Preview shortcut changes without writing files or downloading artwork
python3 qs-steam.py --dry-run

# Inspect the automatic timer
systemctl --user status ryoku-game-sync.timer

# Inspect the most recent sync's logs
journalctl --user -u ryoku-game-sync.service -n 60 --no-pager

# Completely uninstall the synchronizer and its generated shortcuts
~/.local/bin/ryoku-game-sync --uninstall
```

**Important:** `--uninstall` removes shortcuts with the reserved `ryoku-game-` prefix and the synchronizer's own user service/timer. It does **not** uninstall Steam, Lutris, games, prefixes, or artwork you've installed through those applications.

## Artwork and troubleshooting

Check a generated shortcut, replacing the App ID with your own:

```bash
grep -E '^(Name|Exec|Icon)=' ~/.local/share/applications/ryoku-game-steam-<APPID>.desktop
```

If the icon still says `steam` or `lutris`, verify local caches, run the sync again, and check service logs. Steam cover downloads require internet access and may fail for some titles; unsuccessful lookups are retried no more than once a day.

The app runner controls image rendering. QS Steam supplies the `Icon=` field, but **does not turn Ryoku's application-list rows into large cover-art cards**.

If games do not appear, confirm the games are installed (not merely in your Steam library), that Steam or Lutris launches, and that Ryoku is refreshing its application index.

## Paths and behavior

| Purpose | Location |
| --- | --- |
| Installed command | `~/.local/bin/ryoku-game-sync` |
| Generated app entries | `~/.local/share/applications/ryoku-game-*.desktop` |
| Cached downloaded artwork | `~/.cache/ryoku-game-sync/artwork/` |
| Timer | `~/.config/systemd/user/ryoku-game-sync.timer` |
| Service | `~/.config/systemd/user/ryoku-game-sync.service` |

`XDG_DATA_HOME` and `XDG_CACHE_HOME` are respected for shortcuts and downloaded artwork, respectively. If your home directory contains existing game configuration, this tool reads the installed-game manifests and Lutris database; it does not rewrite them.

## Development

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile qs-steam.py
```

The tests use temporary Steam/Lutris fixtures and do not touch your actual game libraries.

## License

No license has been selected yet. All rights are reserved by default until the repository owner adds a license.