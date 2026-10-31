#!/usr/bin/env python3
"""Build independent, deterministic New Cam 1.0 app and boot archives."""
import argparse
import gzip
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

ROOT = Path(__file__).absolute().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _safe_source_file(root, relative):
    root = Path(root)
    if root.is_symlink():
        raise ValueError('symlink source root')
    path = root
    for part in Path(relative).parts:
        if part in ('', '.', '..'):
            raise ValueError('unsafe source path')
        path = path/part
        if path.is_symlink():
            raise ValueError('symlink source input: '+str(path))
    if not path.is_file():
        raise FileNotFoundError('missing source file: '+str(path))
    return path


def _build(contents, prefix, output, executable_names, verify_bytes):
    output = Path(output)
    if output.is_symlink():
        raise ValueError('symlink output directory')
    archive = output/(prefix+'.tar.gz')
    checksum = output/(archive.name+'.sha256')
    if any(path.exists() or path.is_symlink() for path in (archive, checksum)):
        raise FileExistsError('candidate already exists; choose a fresh retained output directory')
    verify_bytes(contents)
    raw = io.BytesIO()
    with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as bundle:
            for name, data in sorted(contents.items()):
                info = tarfile.TarInfo(prefix+'/'+name)
                info.size = len(data)
                info.mode = 0o755 if name in executable_names else 0o644
                info.mtime = 0
                bundle.addfile(info, io.BytesIO(data))
    payload = raw.getvalue()
    output.mkdir(parents=True, exist_ok=True)
    with archive.open('xb') as stream:
        stream.write(payload)
    with checksum.open('x', encoding='ascii') as stream:
        stream.write(_sha(payload)+'  '+archive.name+'\n')
    return archive


def build_app(source, output):
    checker = _load(ROOT/'pi-camera-1.0/check_package.py', 'camera_v1_checker')
    contents = {name: checker.regular_file(source, name).read_bytes()
                for name in checker.RELEASE_FILES}
    contents['MANIFEST.sha256'] = ''.join(_sha(contents[name])+'  '+name+'\n'
                                           for name in sorted(checker.RELEASE_FILES)).encode('ascii')
    archive = _build(contents, checker.PREFIX, output,
                     {name for name in contents if name.endswith('.sh')}, checker.check_contents)
    checker.verify_archive(archive)
    return archive


def build_boot(source, output):
    payload = _load(ROOT/'tools/camera_v1_boot_payload.py', 'camera_v1_boot_payload')
    contents = {name: payload.regular_bytes(_safe_source_file(source, name))
                for name in payload.FILES}
    contents['MANIFEST.sha256'] = ''.join(_sha(contents[name])+'  '+name+'\n'
                                           for name in sorted(payload.FILES)).encode('ascii')
    archive = _build(contents, payload.PREFIX, output, set(), payload.check_contents)
    payload.read_archive(archive)
    return archive


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-source', type=Path, default=ROOT/'pi-camera-1.0')
    parser.add_argument('--boot-source', type=Path, default=ROOT/'pi-boot-logo-1.0')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    for archive in (build_app(args.app_source, args.output),
                    build_boot(args.boot_source, args.output)):
        print(str(archive)+'\nSHA256 '+_sha(archive.read_bytes()))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
