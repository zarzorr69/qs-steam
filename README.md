# qs-steam

**QuickShell Steam** — automatically surface installed games from **Steam, Lutris, Heroic, Bottles, and Hydra** in a QuickShell application launcher, including game icons and artwork.

Initially built for **Ryoku Shell** on **CachyOS + Hyprland**, but the generated [freedesktop.org desktop entries](https://specifications.freedesktop.org/desktop-entry-spec/latest/) can also be used by other app launchers that index XDG applications.

> This is an independent community utility, not affiliated with Valve, Heroic, Lutris, Bottles, Hydra, or Ryoku Shell.

## What it does

- Finds **installed** Steam games, including games in additional Steam library folders (even paths containing spaces).
- Supports native Linux Steam as well as Steam installed via Flatpak.
- Finds installed Lutris games from Lutris' local database; supports native and Flatpak Lutris, and skips Steam-runner duplicates.
- Imports **Heroic** Epic, GOG, and Amazon games from read-only installed-game manifests (native and Flatpak); launches with official `heroic://launch` URLs.
- Queries **Bottles** registered programs through `bottles-cli --json` (native or Flatpak); each entry launches through the original bottle, not directly through Wine.
- Imports **Hydra** exported `.desktop` game shortcuts from your Desktop, retaining Hydra's own `hydralauncher://run` link and runtime settings.
- Can optionally discover **Hydra's LevelDB library** directly when the Python `plyvel` package is available and the database is unlocked; otherwise, use Hydra's *Create desktop shortcut* feature for a game. This is not guaranteed for every Hydra build.
- Imports supported launcher-exported desktop game shortcuts (including certain itch.io, Heroic and Lutris links) without touching the original shortcut. Other launchers already publishing standard XDG application entries show up naturally in Ryoku.
- Uses per-game Steam icons from local icon and library caches (including newer hashed filenames), user grid art where applicable, and existing Lutris icons/covers.
- Falls back to public Steam artwork when a game has no matching local image (no API key needed; at most six new requests per run, cached locally).
- Maintains `.desktop` shortcuts under `~/.local/share/applications/` so the games appear in Ryoku's launcher.
- Adds a **systemd user timer** that refreshes the library after login and every five minutes.
- Removes only obsolete `ryoku-game-*.desktop` shortcuts managed by this utility.

**Launch behavior:** QS Steam delegates launches back to the responsible launcher: Steam URIs, Lutris game IDs, Heroic URLs, Bottles CLI or Hydra deep links. It never guesses Wine/Proton commands from `.exe` paths. Your games, prefixes and compatibility settings stay inside their launchers.

## Requirements

- Linux desktop that reads XDG `.desktop` applications (designed for Ryoku Shell / QuickShell).
- Python **3.10+** (standard library only).
- An installed launcher (Steam, Lutris, Heroic, Bottles or Hydra) and its existing local game metadata. Unsupported launchers can be integrated when they export valid `.desktop` game shortcuts.
- `systemd --user` for automatic background sync. Manual runs do not require a timer.

No `sudo` or pip dependency is needed for Steam, Lutris, Heroic, Bottles, or game shortcut imports. **Automatic Hydra LevelDB discovery is an optional advanced feature** requiring `plyvel` and a readable database; shortcut import works without it.

## Install

```bash
git clone https://github.com/zarzorr69/qs-steam.git
cd qs-steam
python3 qs-steam.py --install
```

This copies the synchronizer to `~/.local/bin/ryoku-game-sync`, enables `ryoku-game-sync.timer` as a user service, and immediately scans your installed games.

**Upgrading from the original `ryoku-game-sync.py` or `ryoku-game-sync-v2.py`:** Just run `python3 qs-steam.py --install`. It replaces the same installed command, timer, and managed shortcut files, retaining compatibility with your existing setup.

## Supported launchers and current limitations

| Launcher | Discovery | Launch method |
| --- | --- | --- |
| Steam | Installed `.acf` manifests, multiple drives, native/Flatpak | Steam URI |
| Lutris | Installed records in local SQLite database | Lutris rungameid URI |
| Heroic (Epic / GOG / Amazon) | Installed JSON indexes, native/Flatpak | `heroic://launch/<runner>/<appid>` via `xdg-open` |
| Bottles | Registered programs via Bottles CLI (requires available CLI) | `bottles-cli run -b ... -p ...` |
| Hydra | Exported desktop shortcuts; optionally, LevelDB via `plyvel` | Hydra launcher-generated Exec / `hydralauncher://run` |
| Other launchers | Standard XDG app shortcuts already appear in Ryoku; supported desktop exports can be imported | Original application command |

**Hydra:** The default install does *not* parse LevelDB because Python does not include a LevelDB reader. To bring a Hydra game into Ryoku without extra Python packages, create its **desktop shortcut in Hydra**; the next five-minute sync imports it. For fully automatic Hydra indexing, `plyvel` is optional and may be unable to open Hydra's database while Hydra holds its lock. This adapter is experimental and has only been tested using fixture data.

**Bottles:** The scanner lists registered programs, which may include non-game Windows applications. It does not scan all Wine prefixes or randomly discovered `.exe` files. Program enumeration invokes Bottles' CLI with timeouts; unsupported or busy installations are skipped without deleting their previous shortcuts.

**Other launchers:** "All game launchers" cannot be guaranteed because some have no stable install metadata or direct per-game launch interface. The core is extensible with additional per-launcher adapters; Ryoku can already show launchers' valid shortcuts in `~/.local/share/applications` without qs-steam copying them.

## Updating from earlier versions

Run the same curl command as above, or `git pull && python3 qs-steam.py --install` in an existing checkout. Existing Steam/Lutris entries and the systemd timer are retained. To preview game changes first, run `python3 qs-steam.py --dry-run`.

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

If the icon still says `steam`, `lutris`, `heroic`, or `hydra`, verify local caches, run the sync again, and check service logs. Steam cover downloads require internet access and may fail for some titles; unsuccessful lookups are retried no more than once a day.

The app runner controls image rendering. QS Steam supplies the `Icon=` field, but **does not turn Ryoku's application-list rows into large cover-art cards**.

If games do not appear, confirm they are installed, the launcher can start them directly, and Ryoku is refreshing its application index. For Hydra, see the shortcut/LevelDB caveat above. For Heroic, `xdg-open heroic://...` must resolve to Heroic.

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

The tests use temporary Steam/Lutris/Heroic/Bottles/Hydra fixtures and do not touch actual libraries. Launcher-specific behavior is not live-verified on all releases.

## License

No license has been selected yet. All rights are reserved by default until the repository owner adds a license.
