"""Build a source installation kit, excluding runtime data, secrets and third-party binaries."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT=Path(__file__).resolve().parents[1]
FILES=['app.py','web.py','events.py','evidence.py','registry_csv.py','mail_delivery.py','ocr_learning.py','ocr_train.py','ocr_backends.py','accuracy_benchmark.py','vision_train.py','plate_geometry.py','plate_rules.py','requirements.txt','Dockerfile','README.md','THIRD_PARTY_NOTICES.md','LICENSE-PROPOSAL.md','.dockerignore']
DIRS=['aigate','templates','static','onprem','scripts','training','licenses','tests','deploy','examples']
FILES.append('plate_pipeline.py')
FILES.extend(['fast_plate_ocr_training.py', 'fast_plate_ocr_train.py'])
SUFFIXES={'.py','.js','.cjs','.html','.css','.md','.txt','.yaml','.json','.ps1','.sh','.csv'}

def build(destination,revision):
    paths=[ROOT/name for name in FILES]
    for directory in DIRS:
        paths.extend(p for p in (ROOT/directory).rglob('*') if p.is_file() and not p.is_symlink()
            and not any(part.startswith('.') or part=='__pycache__' for part in p.relative_to(ROOT).parts)
            and (p.suffix in SUFFIXES or p.name in ('env.example','Caddyfile')))
    manifest={'revision':revision,'kind':'source-installation-kit','project_license':'pending owner selection','files':{}}
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            content=path.read_bytes();name=str(path.relative_to(ROOT))
            manifest['files'][name]=hashlib.sha256(content).hexdigest()
            archive.writestr('AI-Gate-System/'+name,content)
        archive.writestr('AI-Gate-System/PACKAGE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    print(destination.resolve())

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='dist/AI-Gate-System-onprem-source.zip');parser.add_argument('--revision',required=True)
    args=parser.parse_args();build(args.output,args.revision)
