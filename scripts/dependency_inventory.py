"""Capture the installed environment, license files and exact versions for release review."""
import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import subprocess
import sys

def capture(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    entries=[]
    for dist in sorted(metadata.distributions(),key=lambda d:d.metadata['Name'].lower()):
        name=dist.metadata['Name'];version=dist.version
        identifier=re.sub(r'[^A-Za-z0-9_.-]','_',name+'-'+version)
        item={'name':name,'version':version,'license':dist.metadata.get('License-Expression') or dist.metadata.get('License','UNKNOWN'),'license_files':[]}
        for file in dist.files or []:
            if any(part.endswith('.dist-info') for part in file.parts) and (
                'licenses' in file.parts or file.name.upper().startswith(('LICENSE','COPYING','NOTICE'))):
                source=Path(dist.locate_file(file))
                if not source.is_file():continue
                target=output/'licenses'/identifier/Path(*file.parts[1:])
                target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(source.read_bytes())
                item['license_files'].append(str(target.relative_to(output)))
        entries.append(item)
    (output/'inventory.json').write_text(json.dumps({'scope':'All packages in the installation environment, not a compliance certification','packages':entries},ensure_ascii=False,indent=2),encoding='utf-8')
    result=subprocess.run([sys.executable,'-m','pip','freeze','--all'],capture_output=True,text=True,check=True)
    (output/'installed-versions.txt').write_text(result.stdout,encoding='utf-8')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',required=True)
    capture(parser.parse_args().output)
