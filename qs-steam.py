#!/usr/bin/env python3
"""Sync locally installed games from multiple launchers into Ryoku/XDG menus.

No third-party packages, root privileges, or edits to game configuration required.
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import sqlite3
import time
from urllib.parse import quote, urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
APP_DIR = Path(os.environ.get('XDG_DATA_HOME') or HOME / '.local/share') / 'applications'
BIN = HOME / '.local/bin/ryoku-game-sync'
UNIT_DIR = HOME / '.config/systemd/user'
PREFIX = 'ryoku-game-'
ART_DIR = Path(os.environ.get('XDG_CACHE_HOME') or HOME / '.cache') / 'ryoku-game-sync/artwork'
# Limit network requests to avoid delays for users with very large Steam libraries.
ART_DOWNLOAD_BUDGET = 6
art_download_count = 0
ART_RETRY_SECONDS = 24 * 60 * 60


def first_image(paths):
    """Return an absolute image pathname rather than a theme name."""
    for path in paths:
        if path.is_file() and path.stat().st_size:
            return str(path.resolve())
    return None


def official_steam_art(appid: str, allow_download: bool) -> str | None:
    """Use official, public Steam art only as a last resort. No API keys."""
    global art_download_count
    for ext in ('jpg', 'png'):
        image = ART_DIR / f'steam-{appid}.{ext}'
        if image.is_file() and image.stat().st_size > 100:
            return str(image.resolve())
    if not allow_download or art_download_count >= ART_DOWNLOAD_BUDGET:
        return None
    failed_marker = ART_DIR / f'steam-{appid}.unavailable'
    if failed_marker.is_file() and time.time() - failed_marker.stat().st_mtime < ART_RETRY_SECONDS:
        return None
    art_download_count += 1
    ART_DIR.mkdir(parents=True, exist_ok=True)
    for basename in ('library_600x900.jpg', 'header.jpg'):
        url = f'https://shared.fastly.steamstatic.com/store_item_assets/steam/apps/{appid}/{basename}'
        try:
            request = Request(url, headers={'User-Agent': 'ryoku-game-sync/2.0'})
            with urlopen(request, timeout=4) as response:
                # Reject HTML/error pages even if a CDN returns HTTP 200.
                if not response.headers.get('Content-Type', '').lower().startswith('image/'):
                    continue
                image_bytes = response.read(4 * 1024 * 1024 + 1)
            if len(image_bytes) > 4 * 1024 * 1024 or len(image_bytes) < 100:
                continue
            if image_bytes.startswith(b'\xff\xd8\xff'):
                ext = 'jpg'
            elif image_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
                ext = 'png'
            else:
                continue
            destination = ART_DIR / f'steam-{appid}.{ext}'
            staging = ART_DIR / f'steam-{appid}.tmp'
            staging.write_bytes(image_bytes)
            staging.replace(destination)
            failed_marker.unlink(missing_ok=True)
            return str(destination.resolve())
        except (HTTPError, URLError, OSError, TimeoutError):
            continue
    failed_marker.touch()
    return None


def steam_is_tool(name: str) -> bool:
    """Exclude Steam compatibility/runtime packages, not real games containing 'Proton'."""
    low = name.casefold().strip()
    return (
        low.startswith('steam linux runtime')
        or low.startswith('steamworks common redistributables')
        or low in {'steamvr', 'steamvr beta', 'steam controller configs'}
        or bool(re.match(r'^proton (?:experimental|hotfix|next|[0-9])', low))
    )


def path_key(p: Path) -> str:
    return str(p.expanduser().resolve())


def desktop_text(value: object) -> str:
    return str(value).replace('\\', '\\\\').replace('\n', ' ').replace('\r', ' ').strip()


def vdf_field(contents: str, field: str) -> str | None:
    match = re.search(r'^\s*"' + re.escape(field) + r'"\s+"((?:\\.|[^"\\])*)"', contents, re.M | re.I)
    if match:
        return match.group(1).replace('\\"', '"').replace('\\\\', '\\')
    return None


def steam_roots() -> list[tuple[Path, str]]:
    possible = [
        (HOME / '.local/share/Steam', 'native'),
        (HOME / '.steam/steam', 'native'),
        (HOME / '.steam/root', 'native'),
        (HOME / '.var/app/com.valvesoftware.Steam/.local/share/Steam', 'flatpak'),
        (HOME / '.var/app/com.valvesoftware.Steam/data/Steam', 'flatpak'),
    ]
    found: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for p, launch_type in possible:
        if p.is_dir() and path_key(p) not in seen:
            found.append((p, launch_type))
            seen.add(path_key(p))
    return found


def steam_icon(root: Path, appid: str, allow_download: bool = True) -> str:
    """Prefer actual game images (including Steam's new SHA-1 icon layout)."""
    icons_base = HOME / '.local/share/icons/hicolor'
    desktop_icons = [icons_base / size / 'apps' / f'steam_icon_{appid}.png'
                     for size in ('512x512', '256x256', '128x128', '64x64', '48x48', '32x32')]
    # Steam's older flat cache paths, and newer per-app folder with hashed icon filenames.
    cache = root / 'appcache/librarycache'
    candidates = [
        cache / f'{appid}_icon.png', cache / f'{appid}_icon.jpg',
        cache / appid / 'icon.png', cache / appid / 'icon.jpg',
    ]
    appdir = cache / appid
    if appdir.is_dir():
        # Protontricks also looks for these SHA-1 images in modern Steam installs.
        candidates.extend(sorted(p for p in appdir.iterdir()
                                 if re.fullmatch(r'[a-fA-F0-9]{40}\.(?:jpg|jpeg|png)', p.name)))
    # Custom SteamGridDB images installed via Steam's artwork editor.
    grid_images: list[Path] = []
    userdata = root / 'userdata'
    if userdata.is_dir():
        for grid_dir in userdata.glob('*/config/grid'):
            grid_images.extend(grid_dir / f'{appid}{suffix}' for suffix in (
                '_icon.png', '_icon.jpg', 'p.png', 'p.jpg', '.png', '.jpg',
            ))
    local_art = first_image([*desktop_icons, *candidates, *grid_images])
    if local_art:
        return local_art

    # An actual cover is still much more useful than the generic Steam logo.
    fallback_candidates = [
        cache / f'{appid}_library_600x900.jpg',
        cache / f'{appid}_library_600x900.png',
        cache / f'{appid}_header.jpg',
        cache / f'{appid}_header.png',
        cache / appid / 'library_600x900.jpg',
        cache / appid / 'library_600x900.png',
        cache / appid / 'header.jpg',
        cache / appid / 'header.png',
    ]
    # App cache filenames can have variant timestamp/hash suffixes.
    if appdir.is_dir():
        for pattern in ('*library_600x900*', '*header*'):
            fallback_candidates.extend(sorted(appdir.glob(pattern)))
    local_cover = first_image(fallback_candidates)
    if local_cover:
        return local_cover
    return official_steam_art(appid, allow_download=allow_download) or 'steam'


def steam_entries(allow_download: bool = True) -> tuple[dict[str, str], list[str]]:
    expected: dict[str, str] = {}
    warnings: list[str] = []
    roots = steam_roots()
    native_ok = bool(shutil.which('steam'))
    flatpak_ok = bool(shutil.which('flatpak'))
    steam_libraries: dict[str, tuple[Path, Path, str]] = {}
    for root, launch_type in roots:
        libraries = [root]
        folders_file = root / 'steamapps/libraryfolders.vdf'
        if folders_file.is_file():
            try:
                contents = folders_file.read_text(encoding='utf-8-sig', errors='replace')
                for raw in re.findall(r'"path"\s+"((?:\\.|[^"\\])*)"', contents):
                    path = raw.replace('\\"', '"').replace('\\\\', '\\')
                    libraries.append(Path(path).expanduser())
            except OSError as error:
                warnings.append(f'Could not read {folders_file}: {error}')
        for library in libraries:
            if library.is_dir():
                key = path_key(library)
                # Prefer native Steam if both native and Flatpak see the same library.
                if key not in steam_libraries or launch_type == 'native':
                    steam_libraries[key] = (library, root, launch_type)
    for library, root, launch_type in steam_libraries.values():
        steamapps = library / 'steamapps'
        for acf in sorted(steamapps.glob('appmanifest_*.acf')):
            try:
                contents = acf.read_text(encoding='utf-8-sig', errors='replace')
            except OSError as error:
                warnings.append(f'Could not read {acf}: {error}')
                continue
            appid = vdf_field(contents, 'appid')
            name = vdf_field(contents, 'name')
            install_dir = vdf_field(contents, 'installdir')
            if not appid or not appid.isdecimal() or not name or not install_dir:
                continue
            if steam_is_tool(name):
                continue
            # Ignore incomplete/uninstalled manifest leftovers.
            if not (steamapps / 'common' / install_dir).is_dir():
                continue
            if launch_type == 'flatpak':
                if not flatpak_ok:
                    warnings.append('Flatpak Steam data found, but flatpak executable unavailable')
                    continue
                exec_cmd = f'flatpak run com.valvesoftware.Steam steam://rungameid/{appid}'
            else:
                if not native_ok:
                    warnings.append('Native Steam data found, but steam executable unavailable')
                    continue
                exec_cmd = f'steam steam://rungameid/{appid}'
            filename = f'{PREFIX}steam-{appid}.desktop'
            expected[filename] = '\n'.join([
                '[Desktop Entry]',
                'Type=Application',
                f'Name={desktop_text(name)}',
                'GenericName=Steam Game',
                f'Exec={exec_cmd}',
                f'Icon={desktop_text(steam_icon(root, appid, allow_download=allow_download))}',
                'Categories=Game;',
                'Terminal=false',
                'StartupNotify=true',
                '',
            ])
    return expected, warnings


def lutris_icon(slug: str, data_dir: Path) -> str:
    if not slug or '/' in slug or '\\' in slug:
        return 'lutris'
    name = f'lutris_{slug}'
    for icon_root in (HOME / '.local/share/icons/hicolor', data_dir.parent / 'icons/hicolor'):
        for candidate in icon_root.glob(f'*/apps/{name}.*'):
            if candidate.suffix.lower() in ('.png', '.jpg', '.jpeg', '.svg') and candidate.is_file():
                return str(candidate.resolve())
    # Some Lutris installs only have the game's poster/cover downloaded.
    for images_dir in ('coverart', 'covers', 'boxart', 'banners'):
        for ext in ('.png', '.jpg', '.jpeg', '.webp'):
            candidate = data_dir / images_dir / (slug + ext)
            if candidate.is_file():
                return str(candidate.resolve())
    return 'lutris'


def lutris_entries() -> tuple[dict[str, str], list[str], set[str]]:
    expected: dict[str, str] = {}
    warnings: list[str] = []
    skip_cleanup: set[str] = set()
    native = (HOME / '.local/share/lutris/pga.db', 'native')
    flatpak = (HOME / '.var/app/net.lutris.Lutris/data/lutris/pga.db', 'flatpak')
    for db_path, mode in (native, flatpak):
        managed_prefix = f'{PREFIX}lutris-{mode}-'
        if not db_path.is_file():
            continue
        if mode == 'native' and not shutil.which('lutris'):
            warnings.append(f'{db_path} found, but native lutris command unavailable')
            skip_cleanup.add(managed_prefix)
            continue
        if mode == 'flatpak' and not shutil.which('flatpak'):
            warnings.append(f'{db_path} found, but flatpak command unavailable')
            skip_cleanup.add(managed_prefix)
            continue
        try:
            with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=2) as db:
                fields = {column[1] for column in db.execute('PRAGMA table_info(games)')}
                if not {'id', 'name', 'slug', 'runner', 'installed'}.issubset(fields):
                    raise sqlite3.DatabaseError('Unsupported Lutris games table')
                data = list(db.execute('SELECT id, name, slug, runner FROM games WHERE installed = 1'))
        except (OSError, sqlite3.Error) as error:
            warnings.append(f'Could not read Lutris database {db_path}: {error}')
            skip_cleanup.add(managed_prefix)
            continue
        for game_id, name, slug, runner in data:
            if not name or not runner or str(runner).casefold() == 'steam':
                # Steam-managed games will already get Steam launchers.
                continue
            if not str(game_id).isdecimal():
                continue
            if mode == 'native':
                exec_cmd = f'env LUTRIS_SKIP_INIT=1 lutris lutris:rungameid/{game_id}'
            else:
                exec_cmd = f'env LUTRIS_SKIP_INIT=1 flatpak run net.lutris.Lutris lutris:rungameid/{game_id}'
            filename = f'{managed_prefix}{game_id}.desktop'
            expected[filename] = '\n'.join([
                '[Desktop Entry]',
                'Type=Application',
                f'Name={desktop_text(name)}',
                'GenericName=Lutris Game',
                f'Exec={exec_cmd}',
                f'Icon={desktop_text(lutris_icon(str(slug or ""), db_path.parent))}',
                'Categories=Game;',
                'Terminal=false',
                'StartupNotify=true',
                '',
            ])
    return expected, warnings, skip_cleanup




# Optional adapters only read game indexes and never rewrite launcher databases.
# For all adapters, failure to scan an existing data source preserves existing
# qs-steam entries for that source rather than destroying working shortcuts.


def stable_id(value: object) -> str:
    """Stable filename-safe ID, tolerant of punctuation and Unicode."""
    return hashlib.sha256(str(value).encode('utf-8')).hexdigest()[:20]


def desktop_exec_argv(args: list[str]) -> str:
    """Encode argv using freedesktop Exec= quoting (not shell quoting)."""
    def encode(value: str) -> str:
        # '%' starts desktop-file field codes, so escape it as '%%'.
        value = value.replace('%', '%%')
        if re.search(r'[\s"\\\$`&;|<>*?()#]', value):
            for character in ('\\', '"', '$', '`'):
                value = value.replace(character, '\\' + character)
            return '"' + value + '"'
        return value
    return ' '.join(encode(x) for x in args)


def xdg_entry(title: str, source: str, command: str, icon: str) -> str:
    return '\n'.join((
        '[Desktop Entry]', 'Type=Application',
        f'Name={desktop_text(title)}',
        f'GenericName={desktop_text(source)} Game',
        f'Exec={command}',
        f'Icon={desktop_text(icon)}',
        'Categories=Game;', 'Terminal=false', 'StartupNotify=true', ''
    ))


def read_manifest(path: Path):
    """Read only reasonably-sized JSON indexes, never an entire launcher cache."""
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Game index exceeds 16 MiB')
    with path.open('r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def heroic_roots() -> list[tuple[Path, str]]:
    return [
        (HOME / '.config/heroic', 'native'),
        (HOME / '.var/app/com.heroicgameslauncher.hgl/config/heroic', 'flatpak'),
    ]


def heroic_install_rows(document: object, runner: str):
    """Return (app_id, metadata), supporting Epic/GOG/Amazon manifest shapes."""
    if runner == 'legendary' and isinstance(document, dict):
        rows = document.items()
    elif isinstance(document, list):
        rows = ((str(item.get('appName') or item.get('id') or item.get('app_name') or ''), item)
                for item in document if isinstance(item, dict))
    elif isinstance(document, dict):
        nested = document.get('installed')
        if isinstance(nested, list):
            rows = ((str(item.get('appName') or item.get('id') or item.get('app_name') or ''), item)
                    for item in nested if isinstance(item, dict))
        else:
            rows = ((str(k), v) for k, v in document.items() if isinstance(v, dict))
    else:
        return
    for key, value in rows:
        if not isinstance(value, dict) or value.get('is_dlc'):
            continue
        appid = str(value.get('app_name') or value.get('appName') or value.get('id') or key)
        location = value.get('install_path') or value.get('installPath') or value.get('path')
        if not appid or not isinstance(location, str) or not location.strip():
            continue
        # Only installed entries with real directories, not owned library entries.
        if not Path(location).expanduser().is_dir():
            continue
        yield appid, value


def heroic_art(root: Path, appid: str, entry: dict, runner: str) -> str:
    candidates: list[Path] = []
    for key in ('art_square', 'art_cover', 'icon', 'icon_path'):
        v = entry.get(key)
        if isinstance(v, str) and v.startswith('/'):
            candidates.append(Path(v))
    for base in (root / 'images', root / 'icons', root / 'artwork', root / 'cache/images'):
        for suffix in ('.png', '.jpg', '.jpeg', '.webp'):
            candidates.append(base / (appid + suffix))
    return first_image(candidates) or 'heroic'


def heroic_entries() -> tuple[dict[str, str], list[str], set[str]]:
    expected: dict[str, str] = {}
    warnings: list[str] = []
    preserve: set[str] = set()
    specs = (
        ('legendary', ('legendaryConfig/legendary/installed.json', 'legendary/installed.json')),
        ('gog', ('gog_store/installed.json',)),
        ('nile', ('nile_config/nile/installed.json', 'nile_config/installed.json')),
    )
    for root, mode in heroic_roots():
        # AppImage installs register heroic:// without exposing a 'heroic' binary.
        launchable = bool(shutil.which('xdg-open'))
        for runner, relative_paths in specs:
            prefix = f'{PREFIX}heroic-{mode}-{runner}-'
            paths = [root / rel for rel in relative_paths]
            if runner == 'legendary':
                paths += [HOME / '.config/legendary/installed.json'] if mode == 'native' else [
                    HOME / '.var/app/com.heroicgameslauncher.hgl/config/legendary/installed.json'
                ]
            manifests = [path for path in paths if path.is_file()]
            if not manifests:
                continue
            if not launchable:
                warnings.append(f'Heroic {mode} manifest exists but xdg-open unavailable')
                preserve.add(prefix)
                continue
            for path in manifests:
                try:
                    document = read_manifest(path)
                    for appid, game in heroic_install_rows(document, runner):
                        title = str(game.get('title') or game.get('name') or game.get('gameTitle') or appid)
                        url = f'heroic://launch/{runner}/{quote(appid, safe="")}'
                        name = f'{prefix}{stable_id(appid)}.desktop'
                        # xdg-open retains Heroic's own chosen Wine/Proton game settings.
                        expected[name] = xdg_entry(title, f'Heroic {runner.upper()}',
                                                  desktop_exec_argv(['xdg-open', url]), heroic_art(root, appid, game, runner))
                except (OSError, ValueError, UnicodeError, json.JSONDecodeError) as error:
                    warnings.append(f'Cannot read Heroic game index {path}: {error}')
                    preserve.add(prefix)
    return expected, warnings, preserve


def bottles_command(args: list[str], mode: str):
    if mode == 'flatpak':
        return ['flatpak', 'run', '--command=bottles-cli', 'com.usebottles.bottles', *args]
    return ['bottles-cli', *args]


def bottles_json(args: list[str], mode: str):
    result = subprocess.run(bottles_command(['--json', *args], mode),
                            capture_output=True, text=True, timeout=8, check=True)
    return json.loads(result.stdout)


def bottles_entries() -> tuple[dict[str, str], list[str], set[str]]:
    expected: dict[str, str] = {}
    warnings: list[str] = []
    preserve: set[str] = set()
    for mode, present in (
        ('native', bool(shutil.which('bottles-cli')) and
         (HOME / '.local/share/bottles').is_dir()),
        ('flatpak', bool(shutil.which('flatpak')) and
         (HOME / '.var/app/com.usebottles.bottles').is_dir()),
    ):
        prefix = f'{PREFIX}bottles-{mode}-'
        if not present:
            continue
        try:
            available = bottles_json(['list', 'bottles'], mode)
            if isinstance(available, dict):
                available = available.get('bottles', [])
            if not isinstance(available, list):
                raise ValueError('Unknown Bottles list format')
            for bottle in available[:15]:
                bottle_name = bottle.get('name') if isinstance(bottle, dict) else bottle
                if not isinstance(bottle_name, str) or not bottle_name:
                    continue
                programs = bottles_json(['programs', '-b', bottle_name], mode)
                if not isinstance(programs, list):
                    raise ValueError('Unknown Bottles programs format')
                for program in programs:
                    if not isinstance(program, dict) or program.get('removed'):
                        continue
                    name = program.get('name')
                    if not isinstance(name, str) or not name.strip():
                        continue
                    # Launch the registered program by name using its own bottle.
                    argv = bottles_command(['run', '-b', bottle_name, '-p', name], mode)
                    command = desktop_exec_argv(argv)
                    image = program.get('icon')
                    icon = str(Path(image).resolve()) if isinstance(image, str) and Path(image).is_file() else 'com.usebottles.bottles'
                    ident = stable_id(f'{bottle_name}\0{name}')
                    expected[f'{prefix}{ident}.desktop'] = xdg_entry(name, f'Bottles ({bottle_name})', command, icon)
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as error:
            warnings.append(f'Bottles {mode} scan failed: {error}')
            preserve.add(prefix)
    return expected, warnings, preserve


def desktop_directories() -> list[Path]:
    """Discover user desktop shortcut exports without recursively walking home."""
    candidates = [HOME / 'Desktop', HOME / 'Bureau', HOME / 'Schreibtisch']
    dirs_file = HOME / '.config/user-dirs.dirs'
    if dirs_file.is_file():
        try:
            for line in dirs_file.read_text(encoding='utf-8').splitlines():
                if line.startswith('XDG_DESKTOP_DIR='):
                    value = line.split('=', 1)[1].strip().strip('"')
                    value = value.replace('$HOME', str(HOME))
                    if Path(value).is_absolute():
                        candidates.append(Path(value))
        except OSError:
            pass
    result, seen = [], set()
    for p in candidates:
        if p.is_dir() and path_key(p) not in seen:
            result.append(p)
            seen.add(path_key(p))
    return result


def imported_shortcut(path: Path):
    """Import only known game-launcher commands, not arbitrary Desktop scripts."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    with path.open('r', encoding='utf-8-sig') as stream:
        parser.read_file(stream)
    if not parser.has_section('Desktop Entry'):
        return None
    record = parser['Desktop Entry']
    if (record.get('Type') != 'Application' or
        record.get('Hidden', 'false').casefold() == 'true' or
        record.get('NoDisplay', 'false').casefold() == 'true'):
        return None
    cmd = record.get('Exec', '').strip()
    name = record.get('Name', '').strip()
    if not cmd or not name:
        return None
    # The official Hydra deep link is hydralauncher://run?shop=...&objectId=...
    # No guessing executables or bypassing the game's selected runtime.
    low = cmd.lower()
    sources = (
        ('hydralauncher://run?', 'Hydra'),
        ('heroic://launch/', 'Heroic'),
        ('lutris:rungameid/', 'Lutris'),
        ('itch://install?', 'itch.io'),
    )
    source = next((label for needle, label in sources if needle in low), None)
    if source is None:
        return None
    if source == 'Hydra' and 'objectid=' not in low:
        return None
    # Keep the launcher-generated Exec string intact (including its Proton flags).
    return source, xdg_entry(name, source, cmd, record.get('Icon', source.lower()))


def exported_shortcut_entries() -> tuple[dict[str, str], list[str], set[str]]:
    expected: dict[str, str] = {}
    warnings: list[str] = []
    preserve: set[str] = set()
    for directory in desktop_directories():
        for path in sorted(directory.glob('*.desktop')):
            try:
                if path.stat().st_size > 64 * 1024:
                    continue
                found = imported_shortcut(path)
                if found:
                    source, entry = found
                    filename = f'{PREFIX}exported-{source.lower().replace(".", "")}-{stable_id(str(path))}.desktop'
                    expected[filename] = entry
            except (OSError, configparser.Error, UnicodeError) as error:
                warnings.append(f'Could not read game shortcut {path}: {error}')
                preserve.add(f'{PREFIX}exported-')
    return expected, warnings, preserve


def hydra_entries() -> tuple[dict[str, str], list[str], set[str]]:
    """Read Hydra's LevelDB when the optional plyvel binding is available.

    Otherwise Hydra-generated desktop shortcuts are imported by the generic
    adapter. The database is never written to or migrated.
    """
    expected: dict[str, str] = {}
    warnings: list[str] = []
    preserve: set[str] = set()
    paths = [
        HOME / '.config/hydra/hydra-db',
        HOME / '.config/Hydra/hydra-db',
        HOME / '.config/hydra-launcher/hydra-db',
        HOME / '.var/app/gg.hydralauncher.hydra/config/hydra/hydra-db',
    ]
    present = [p for p in paths if p.is_dir() and (p / 'CURRENT').exists()]
    if not present:
        return expected, warnings, preserve
    try:
        import plyvel  # type: ignore  # optional, not part of base install
    except ImportError:
        # This is expected on pure-stdlib installations; don't report a sync
        # failure or remove formerly generated entries.
        preserve.add(f'{PREFIX}hydra-db-')
        return expected, warnings, preserve
    for path in present:
        try:
            db = plyvel.DB(str(path), create_if_missing=False)
            try:
                # abstract-level encodes games sublevel keys with a prefix.
                for key, raw in db:
                    if b'!games!' not in key:
                        continue
                    try:
                        game = json.loads(raw)
                    except (UnicodeError, ValueError):
                        continue
                    if not isinstance(game, dict) or game.get('isDeleted'):
                        continue
                    shop, objectid = game.get('shop'), game.get('objectId')
                    name = game.get('title')
                    if not all(isinstance(x, str) and x for x in (shop, objectid, name)):
                        continue
                    executable = game.get('executablePath')
                    if (shop != 'launchbox' and
                        (not isinstance(executable, str) or not Path(executable).is_file())):
                        continue
                    url = 'hydralauncher://run?' + urlencode({'shop': shop, 'objectId': objectid})
                    icon = 'hydra'
                    asset_dir = path.parent / 'Assets' / f'{shop}-{objectid}'
                    candidates = []
                    for item in ('cover.jpg', 'icon.png', 'hero.jpg', 'library.jpg'):
                        candidates.append(asset_dir / item)
                    icon = first_image(candidates) or icon
                    filename = f'{PREFIX}hydra-db-{stable_id(shop + ":" + objectid)}.desktop'
                    expected[filename] = xdg_entry(name, 'Hydra', desktop_exec_argv(['xdg-open', url]), icon)
            finally:
                db.close()
        except Exception as error:
            # Different plyvel builds expose distinct DB/lock exceptions.
            warnings.append(f'Hydra database {path} is currently unavailable: {error}')
            preserve.add(f'{PREFIX}hydra-db-')
    return expected, warnings, preserve


def scan_game_launchers(allow_download: bool = True) -> tuple[dict[str, str], list[str], set[str], dict[str, int]]:
    result: dict[str, str] = {}
    warnings: list[str] = []
    preserve: set[str] = set()
    totals: dict[str, int] = {}
    for name, scanner in (
        ('Steam', lambda: (*steam_entries(allow_download=allow_download), set())),
        ('Lutris', lutris_entries),
        ('Heroic', heroic_entries),
        ('Bottles', bottles_entries),
        ('Hydra', hydra_entries),
        ('Desktop exports', exported_shortcut_entries),
    ):
        entries, messages, kept = scanner()
        result.update(entries)
        warnings.extend(messages)
        preserve.update(kept)
        totals[name] = len(entries)
        if name == 'Steam' and messages and not entries:
            preserve.add(f'{PREFIX}steam-')
    return result, warnings, preserve, totals


def sync(dry_run: bool = False) -> int:
    global art_download_count
    art_download_count = 0
    # Dry runs must never download artwork.
    expected, warnings, skip_cleanup, totals = scan_game_launchers(allow_download=not dry_run)
    if not dry_run:
        APP_DIR.mkdir(parents=True, exist_ok=True)
    changed = 0
    for name, contents in sorted(expected.items()):
        target = APP_DIR / name
        try:
            old = target.read_text(encoding='utf-8') if target.exists() else None
            if old == contents:
                continue
            if dry_run:
                print(f'Would create/update: {name}')
            else:
                tmp = APP_DIR / (name + '.tmp')
                tmp.write_text(contents, encoding='utf-8')
                tmp.chmod(0o644)
                tmp.replace(target)
                print(f'Updated: {name}')
            changed += 1
        except OSError as error:
            warnings.append(f'Failed to write {target}: {error}')
    if APP_DIR.is_dir():
        for old in sorted(APP_DIR.glob(f'{PREFIX}*.desktop')):
            if old.name in expected or any(old.name.startswith(p) for p in skip_cleanup):
                continue
            if dry_run:
                print(f'Would remove obsolete: {old.name}')
            else:
                try:
                    old.unlink()
                    print(f'Removed obsolete: {old.name}')
                except OSError as error:
                    warnings.append(f'Failed to remove {old}: {error}')
                    continue
            changed += 1
    print(' | '.join(f'{name}: {count}' for name, count in totals.items()) + f' | Changes: {changed}')
    for warning in sorted(set(warnings)):
        print(f'Warning: {warning}', file=sys.stderr)
    if not expected:
        print('No launchable games found. Verify installed game libraries or export game desktop shortcuts.')
    return 0 if not warnings else 1


def unit_text() -> tuple[str, str]:
    service = '''[Unit]
Description=Synchronize games from launchers into Ryoku app launcher

[Service]
Type=oneshot
ExecStart=%h/.local/bin/ryoku-game-sync
'''
    timer = '''[Unit]
Description=Refresh Ryoku game shortcuts automatically

[Timer]
OnStartupSec=20s
OnUnitActiveSec=5min
AccuracySec=30s
Unit=ryoku-game-sync.service

[Install]
WantedBy=timers.target
'''
    return service, timer


def install() -> int:
    if os.geteuid() == 0:
        print('Do not run as root or with sudo.', file=sys.stderr)
        return 1
    BIN.parent.mkdir(parents=True, exist_ok=True)
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve()
    if source != BIN.resolve():
        shutil.copyfile(source, BIN)
    BIN.chmod(0o755)
    service, timer = unit_text()
    (UNIT_DIR / 'ryoku-game-sync.service').write_text(service)
    (UNIT_DIR / 'ryoku-game-sync.timer').write_text(timer)
    for cmd in (
        ['systemctl', '--user', 'daemon-reload'],
        ['systemctl', '--user', 'enable', '--now', 'ryoku-game-sync.timer'],
    ):
        try:
            subprocess.run(cmd, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as error:
            print(f'Could not enable automatic sync: {error}', file=sys.stderr)
            print(f'You can still run {BIN} manually.')
            return sync()
    print('Installed and enabled the automatic 5-minute game sync.')
    return sync()


def uninstall() -> int:
    if os.geteuid() == 0:
        print('Do not run as root or with sudo.', file=sys.stderr)
        return 1
    subprocess.run(['systemctl', '--user', 'disable', '--now', 'ryoku-game-sync.timer'], check=False)
    for unit in ('ryoku-game-sync.service', 'ryoku-game-sync.timer'):
        (UNIT_DIR / unit).unlink(missing_ok=True)
    subprocess.run(['systemctl', '--user', 'daemon-reload'], check=False)
    count = 0
    if APP_DIR.exists():
        for p in APP_DIR.glob(f'{PREFIX}*.desktop'):
            p.unlink()
            count += 1
    BIN.unlink(missing_ok=True)
    print(f'Removed sync and {count} managed shortcuts. Steam/Lutris unaffected.')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--install', action='store_true', help='Install and enable user-level auto sync')
    mode.add_argument('--uninstall', action='store_true', help='Undo changes made by this tool')
    mode.add_argument('--dry-run', action='store_true', help='Preview changes')
    args = parser.parse_args()
    if args.install:
        return install()
    if args.uninstall:
        return uninstall()
    return sync(args.dry_run)


if __name__ == '__main__':
    raise SystemExit(main())
