"""Build the portable OKWW package using only the Python standard library."""
import ast
import hashlib
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED


ROOT = Path(__file__).resolve().parent
PROJECT_NAME = 'ok-ww-assist'
SCRIPT_NAME = '通用辅助'
PACKAGE_ID = 'AutoRechallenge'  # Keep existing installations/settings compatible.
RUNTIME_FILES = ('AutoRechallenge.py', 'ArMedicine.py', 'MedicineMonitor.py', 'MedicineFarmEcho.py')
TASKS = [[name, name] for name in ('AutoRechallenge', 'MedicineMonitor', 'MedicineFarmEcho')]


def build():
    source = ROOT / 'AutoRechallenge.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'AutoRechallenge')
    version = next(ast.literal_eval(n.value) for n in cls.body if isinstance(n, ast.Assign)
                   and n.targets[0].id == 'VERSION')
    manifest = {'file_name': PACKAGE_ID, 'script_name': SCRIPT_NAME,
                'version': version, 'tasks': TASKS}
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (ROOT / 'manifest.json').write_bytes(manifest_bytes)
    output = ROOT / 'release'
    output.mkdir(exist_ok=True)
    package = output / f'{PROJECT_NAME}-{version}.okscript'
    runtime = {}
    for name in RUNTIME_FILES:
        path = ROOT / name
        # Match .gitattributes so installation and Git use identical bytes.
        data = path.read_bytes().replace(b'\r\n', b'\n')
        compile(data.decode('utf-8'), name, 'exec')
        if path.read_bytes() != data:
            path.write_bytes(data)
        runtime[name] = data
    runtime['manifest.json'] = manifest_bytes
    # Match the reference plugin: .okscript has files at its root, while the
    # release .zip has one enclosing folder named after the stable package ID.
    archives = (package, output / f'{PROJECT_NAME}-{version}.zip')
    for archive_path in archives:
        prefix = PACKAGE_ID + '/' if archive_path.suffix == '.zip' else ''
        with ZipFile(archive_path, 'w', ZIP_DEFLATED) as archive:
            for name, data in runtime.items():
                archive.writestr(prefix + name, data)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in (*(ROOT / name for name in RUNTIME_FILES), ROOT / 'manifest.json', *archives)}
    (output / 'checksums.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    for archive_path in archives:
        print(archive_path)
    return package


if __name__ == '__main__':
    build()
