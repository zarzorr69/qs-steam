"""Local-fixture checks. No real Steam/Lutris files or network required."""

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / 'qs-steam.py'
SPEC = importlib.util.spec_from_file_location('qs_steam', SOURCE)
qs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qs)


class QSSteamTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.app_dir = self.home / '.local/share/applications'
        self.root = self.home / '.local/share/Steam'
        self.art_dir = self.home / '.cache/ryoku-game-sync/artwork'
        self.patches = [
            mock.patch.object(qs, 'HOME', self.home),
            mock.patch.object(qs, 'APP_DIR', self.app_dir),
            mock.patch.object(qs, 'ART_DIR', self.art_dir),
            mock.patch.object(qs, 'art_download_count', 0),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def create_game(self, library, appid='12345', name='Example Game'):
        apps = library / 'steamapps'
        (apps / 'common' / name).mkdir(parents=True, exist_ok=True)
        (apps / f'appmanifest_{appid}.acf').write_text(
            f'"AppState"\n{{\n"appid" "{appid}"\n"name" "{name}"\n'
            f'"installdir" "{name}"\n}}\n', encoding='utf-8'
        )

    def test_steam_library_with_spaces_and_local_icon(self):
        lib = self.home / 'Games With Spaces'
        self.root.joinpath('steamapps').mkdir(parents=True)
        (self.root / 'steamapps/libraryfolders.vdf').write_text(
            f'"libraryfolders"\n{{\n "0" {{ "path" "{lib}" }}\n}}\n'
            .replace('"path"', '\n"path"')
        )
        self.create_game(lib)
        icon = self.root / 'appcache/librarycache/12345/icon.png'
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
        with mock.patch.object(qs.shutil, 'which', return_value='/usr/bin/steam'):
            entries, warnings = qs.steam_entries(allow_download=False)
        self.assertFalse(warnings)
        shortcut = entries['ryoku-game-steam-12345.desktop']
        self.assertIn('Name=Example Game', shortcut)
        self.assertIn('Exec=steam steam://rungameid/12345', shortcut)
        self.assertIn(f'Icon={icon}', shortcut)

    def test_hashed_steam_icon(self):
        self.root.mkdir(parents=True)
        icon = self.root / 'appcache/librarycache/99' / ('a' * 40 + '.jpg')
        icon.parent.mkdir(parents=True)
        icon.write_bytes(b'\xff\xd8\xfffixture')
        self.assertEqual(qs.steam_icon(self.root, '99', allow_download=False), str(icon))

    def test_lutris_local_database_and_game_art(self):
        db = self.home / '.local/share/lutris/pga.db'
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as con:
            con.execute('CREATE TABLE games(id INTEGER, name TEXT, slug TEXT, runner TEXT, installed INTEGER)')
            con.execute('INSERT INTO games VALUES (7, ?, ?, ?, 1)', ('Lutris Example', 'lutris-example', 'wine'))
            con.execute('INSERT INTO games VALUES (8, ?, ?, ?, 1)', ('Steam Example', 'steam-example', 'steam'))
        art = db.parent / 'coverart/lutris-example.png'
        art.parent.mkdir()
        art.write_bytes(b'fixture-image')
        with mock.patch.object(qs.shutil, 'which', return_value='/usr/bin/lutris'):
            entries, warnings, preserve = qs.lutris_entries()
        self.assertFalse(warnings)
        self.assertFalse(preserve)
        self.assertEqual(len(entries), 1)
        shortcut = entries['ryoku-game-lutris-native-7.desktop']
        self.assertIn('Icon=' + str(art), shortcut)
        self.assertIn('lutris:rungameid/7', shortcut)

    def test_dry_run_does_not_modify_entries(self):
        self.create_game(self.root)
        with mock.patch.object(qs.shutil, 'which', return_value='/usr/bin/steam'):
            with mock.patch.object(qs, 'steam_icon', return_value='steam'):
                code = qs.sync(dry_run=True)
        self.assertEqual(code, 0)
        self.assertFalse(self.app_dir.exists())


if __name__ == '__main__':
    unittest.main()
