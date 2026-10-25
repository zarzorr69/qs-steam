"""Fixture-only tests of optional multi-launcher adapters."""
import importlib.util
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / 'qs-steam.py'
spec = importlib.util.spec_from_file_location('qs_steam_adapters', SCRIPT)
qs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qs)

class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory()
        self.addCleanup(self.t.cleanup)
        self.h = Path(self.t.name)
        self.d = self.h / '.local/share/applications'
        for variable, value in [('HOME', self.h), ('APP_DIR', self.d),
                                ('ART_DIR', self.h / 'artwork')]:
            p = mock.patch.object(qs, variable, value)
            p.start(); self.addCleanup(p.stop)

    def write_json(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    def test_heroic_all_three_stores_only_installed(self):
        root = self.h / '.config/heroic'
        installed = self.h / 'Games with spaces'; installed.mkdir()
        location = str(installed)
        self.write_json(root/'legendaryConfig/legendary/installed.json', {
            'EpicAlpha': {'app_name':'EpicAlpha','title':'Epic A','install_path':location},
            'notinstalled':{'title':'Library only','install_path':'/not/an/installation'},
            'dlc':{'title':'DLC', 'install_path':location,'is_dlc':True}})
        self.write_json(root/'gog_store/installed.json', {'installed':[
            {'appName':'gog.game', 'title':'GOG Game', 'install_path':location}]})
        self.write_json(root/'nile_config/nile/installed.json', [
            {'id':'amzn.game','title':'Amazon Game','path':location}])
        with mock.patch.object(qs.shutil, 'which', return_value='/usr/bin/xdg-open'):
            entries, warnings, preserve = qs.heroic_entries()
        self.assertFalse(warnings)
        self.assertEqual(len(entries), 3)
        self.assertTrue(all('GenericName=Heroic' in x for x in entries.values()))
        self.assertTrue(any('heroic://launch/legendary/EpicAlpha' in x for x in entries.values()))
        self.assertTrue(any('heroic://launch/gog/gog.game' in x for x in entries.values()))
        self.assertTrue(any('heroic://launch/nile/amzn.game' in x for x in entries.values()))

    def test_heroic_bad_json_preserves_previous_entries(self):
        broken = self.h/'.config/heroic/gog_store/installed.json'
        broken.parent.mkdir(parents=True)
        broken.write_text('{broken')
        with mock.patch.object(qs.shutil,'which',return_value='/usr/bin/xdg-open'):
            found, warnings, preserve = qs.heroic_entries()
        self.assertFalse(found)
        self.assertTrue(warnings)
        self.assertIn('ryoku-game-heroic-native-gog-', preserve)

    def test_hydra_shortcut_from_desktop(self):
        desktop = self.h/'Desktop'
        desktop.mkdir()
        (desktop/'Hydra Game.desktop').write_text('''[Desktop Entry]
Type=Application
Name=Hydra Sample
Exec=/opt/hydra/hydra "hydralauncher://run?shop=steam&objectId=123"
Icon=/tmp/hydra-icon.png
''')
        (desktop/'random.desktop').write_text('''[Desktop Entry]
Type=Application
Name=Some Script
Exec=sh -c bad
''')
        entries, warnings, preserve = qs.exported_shortcut_entries()
        self.assertFalse(warnings)
        self.assertEqual(len(entries), 1)
        value = next(iter(entries.values()))
        self.assertIn('Hydra Sample', value)
        self.assertIn('Exec=/opt/hydra/hydra "hydralauncher://run?shop=steam&objectId=123"', value)
        self.assertTrue(next(iter(entries)).startswith('ryoku-game-exported-hydra-'))

    def test_bottles_cli_preserves_registered_program(self):
        (self.h/'.local/share/bottles').mkdir(parents=True)
        def mock_json(args, mode):
            return ['Gaming'] if args[0] == 'list' else [
                {'name':'The Game Deluxe','icon':'','removed':False}]
        with mock.patch.object(qs.shutil,'which',return_value='/usr/bin/bottles-cli'):
            with mock.patch.object(qs,'bottles_json',side_effect=mock_json):
                entries, warnings, preserve = qs.bottles_entries()
        self.assertFalse(warnings)
        self.assertEqual(len(entries), 1)
        entry=next(iter(entries.values()))
        self.assertIn('Name=The Game Deluxe',entry)
        self.assertIn('bottles-cli run -b Gaming -p "The Game Deluxe"', entry)

    def test_hydra_optional_db_fallback_never_deletes(self):
        db = self.h/'.config/hydra/hydra-db'
        db.mkdir(parents=True)
        (db/'CURRENT').write_text('MANIFEST')
        with mock.patch.dict(sys.modules, {'plyvel': None}):
            found, warnings, preserved=qs.hydra_entries()
        self.assertFalse(found)
        self.assertFalse(warnings)
        self.assertIn('ryoku-game-hydra-db-',preserved)

    def test_hydra_database_is_read_only_and_uses_deep_link(self):
        dbpath=self.h/'.config/hydra/hydra-db'
        dbpath.mkdir(parents=True)
        (dbpath/'CURRENT').write_text('MANIFEST')
        executable=self.h/'My Fancy Game.exe'; executable.write_bytes(b'content')
        class FakeDB:
            def __init__(self,path,create_if_missing=False):
                assert not create_if_missing
                self.path=path
            def __iter__(self):
                yield b'!games!steam:77',json.dumps({'shop':'steam','objectId':'77',
                    'title':'Hydra Game','executablePath':str(executable)}).encode()
            def close(self):pass
        with mock.patch.dict(sys.modules,{'plyvel':types.SimpleNamespace(DB=FakeDB)}):
            entries,warnings,preserve=qs.hydra_entries()
        self.assertFalse(warnings)
        self.assertEqual(len(entries),1)
        value=next(iter(entries.values()))
        self.assertIn('Name=Hydra Game', value)
        self.assertIn('hydralauncher://run?',value)
        self.assertIn('objectId=77',value)

    def test_exec_quoting_escapes_percent(self):
        value=qs.desktop_exec_argv(['xdg-open','hydralauncher://run?shop=custom&objectId=hello world'])
        self.assertEqual(value,'xdg-open "hydralauncher://run?shop=custom&objectId=hello world"')
        self.assertIn('%%20',qs.desktop_exec_argv(['xdg-open','a%20b']))

if __name__ == '__main__':
    unittest.main()
