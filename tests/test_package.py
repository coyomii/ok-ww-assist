import hashlib
import json
from pathlib import Path
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


class PackageTests(unittest.TestCase):
    def test_package_is_portable_and_has_only_runtime_files(self):
        with ZipFile(ROOT / 'release/ok-ww-assist-1.0.1.okscript') as package:
            self.assertEqual(set(package.namelist()), {'AutoRechallenge.py', 'ArMedicine.py',
                             'MedicineMonitor.py', 'MedicineFarmEcho.py', 'manifest.json'})
            self.assertEqual(package.read('AutoRechallenge.py'), (ROOT / 'AutoRechallenge.py').read_bytes())
            self.assertEqual(package.read('manifest.json'), (ROOT / 'manifest.json').read_bytes())
            manifest = json.loads(package.read('manifest.json'))
            self.assertEqual(manifest['file_name'], 'AutoRechallenge')
            self.assertEqual(manifest['version'], '1.0.1')
            self.assertEqual(manifest['script_name'], '通用辅助')
            self.assertEqual(manifest['tasks'], [[name, name] for name in
                             ('AutoRechallenge', 'MedicineMonitor', 'MedicineFarmEcho')])
            source = package.read('AutoRechallenge.py').decode('utf-8')
            compile(source, 'AutoRechallenge.py', 'exec')
            for forbidden in ('Chisa__Hiyuki__Lucilla', 'reset_team_axis', 'drive_team_axis', 'C:\\'):
                self.assertNotIn(forbidden, source)
            for name in package.namelist():
                self.assertEqual(package.read(name), (ROOT / name).read_bytes())

    def test_release_zip_has_same_payload_inside_package_folder(self):
        with ZipFile(ROOT / 'release/ok-ww-assist-1.0.1.okscript') as script, \
                ZipFile(ROOT / 'release/ok-ww-assist-1.0.1.zip') as release:
            self.assertEqual(set(release.namelist()),
                             {'AutoRechallenge/' + name for name in script.namelist()})
            for name in script.namelist():
                self.assertEqual(script.read(name), release.read('AutoRechallenge/' + name))

    def test_release_checksums(self):
        hashes = json.loads((ROOT / 'release/checksums.json').read_text(encoding='utf-8'))
        for name, expected in hashes.items():
            path = ROOT / ('release' if name.endswith(('.okscript', '.zip')) else '') / name
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected, name)
