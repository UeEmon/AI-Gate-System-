"""Verify a ZIP or unpacked package before inference, without ML dependencies."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import zipfile


def validate(location):
    location=Path(location)
    archive=zipfile.ZipFile(location) if location.is_file() else None
    try:
        read=(archive.read if archive else lambda name:(location/name).read_bytes())
        manifest=json.loads(read('manifest.json'))
        if manifest.get('schema_version') != 1:
            raise ValueError('Unsupported package format')
        files=manifest['files']
        required={'models/plate.pt','models/paddle-inference/inference.pdiparams','recognize.py','evaluation.json'}
        if not required.issubset(files) or not any(n in files for n in ('models/paddle-inference/inference.json','models/paddle-inference/inference.pdmodel')):
            raise ValueError('Missing inference artifacts')
        for name,expected in files.items():
            p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or '\\' in name:
                raise ValueError('Unsafe package path')
            if not archive and not (location/name).resolve().is_relative_to(location.resolve()):
                raise ValueError('Unsafe file reference')
            data=read(name)
            if len(data)!=expected['size'] or hashlib.sha256(data).hexdigest()!=expected['sha256']:
                raise ValueError('Checksum mismatch: '+name)
        if archive and (len(set(archive.namelist()))!=len(archive.namelist()) or set(archive.namelist()) != set(files)|{'manifest.json'}):
            raise ValueError('Unrecorded or duplicate archive files')
        return dict(valid=True,model_id=manifest['model_id'],version=manifest['version'],license=manifest['license'])
    finally:
        if archive:
            archive.close()


if __name__=='__main__':
    try:
        print(json.dumps(validate(sys.argv[1]),ensure_ascii=False))
    except (IndexError,OSError,ValueError,KeyError,zipfile.BadZipFile) as error:
        print('INVALID: '+str(error),file=sys.stderr);raise SystemExit(1)
