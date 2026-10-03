"""Publish immutable, verified model versions using GitHub's Git data API."""
import base64
import json
import hashlib
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import zipfile

from model_package import verify_package


class GitHubModels:
    def __init__(self,repository,token):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository):
            raise ValueError('モデルリポジトリ名が不正です。')
        self.repository,self.token = repository,token

    def request(self,method,path,payload=None):
        request = Request('https://api.github.com/repos/'+self.repository+'/'+path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          method=method,headers={'Authorization':'Bearer '+self.token,
                          'Accept':'application/vnd.github+json','Content-Type':'application/json',
                          'X-GitHub-Api-Version':'2022-11-28'})
        try:
            with urlopen(request,timeout=60) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f'GitHubへのモデル登録に失敗しました（HTTP {error.code}）。権限・競合・ファイルサイズを確認してください。') from None
        except URLError:
            raise RuntimeError('GitHubへ接続できません。') from None

    def publish(self,path):
        manifest = verify_package(path)
        if manifest.get('license') in (None,'','UNRESOLVED'):
            raise ValueError('GATE_MODEL_EXPORT_LICENSEに確認済みのモデル利用条件を設定してください。')
        prefix = 'models/'+manifest['version']+'/'
        repo = self.request('GET','')
        branch = repo['default_branch']
        from urllib.parse import quote
        branch_path=quote(branch,safe='')
        head = self.request('GET','git/ref/heads/'+branch_path)['object']['sha']
        base = self.request('GET','git/commits/'+head)['tree']['sha']
        tree = self.request('GET','git/trees/'+base+'?recursive=1')
        if tree.get('truncated'):
            raise ValueError('リポジトリ一覧が大きすぎるため上書き確認ができません。')
        existing = {item['path']:item.get('sha') for item in tree['tree'] if item['type']=='blob'}
        if any(name.startswith(prefix) for name in existing):
            archived = self.request('GET','contents/'+prefix+'manifest.json?ref='+quote(head,safe=''))
            old = json.loads(base64.b64decode(archived['content']))
            with zipfile.ZipFile(path) as archive:
                matching=all(existing.get(prefix+name)==hashlib.sha1(b'blob '+str(len(archive.read(name))).encode()+b'\0'+archive.read(name)).hexdigest() for name in manifest['files'])
            if old['files'] != manifest['files'] or not matching:
                raise ValueError('同じバージョンの異なる内容は上書きできません。')
            return dict(state='already_published',repository=self.repository,version=manifest['version'],commit=head,
                        url='https://github.com/'+self.repository+'/tree/'+quote(branch,safe='')+'/'+prefix)
        items=[]
        with zipfile.ZipFile(path) as archive:
            if any(x.file_size >= 100*1024**2 for x in archive.infolist()):
                raise ValueError('100 MiB以上のファイルはGit LFS又はGitHub Releaseで管理してください。')
            for name in archive.namelist():
                blob=self.request('POST','git/blobs',dict(content=base64.b64encode(archive.read(name)).decode(),encoding='base64'))
                items.append(dict(path=prefix+name,mode='100644',type='blob',sha=blob['sha']))
        created=self.request('POST','git/trees',dict(base_tree=base,tree=items))['sha']
        commit=self.request('POST','git/commits',dict(message='Add evaluated Japanese plate model '+manifest['version'],tree=created,parents=[head]))['sha']
        # A concurrent update fails safely. Never force another person's commit away.
        self.request('PATCH','git/refs/heads/'+branch_path,dict(sha=commit,force=False))
        return dict(state='published',repository=self.repository,version=manifest['version'],commit=commit,
                    url='https://github.com/'+self.repository+'/tree/'+quote(branch,safe='')+'/'+prefix)


def publish_from_environment(path):
    token=os.getenv('GATE_MODEL_REPO_TOKEN')
    if not token:
        raise ValueError('GATE_MODEL_REPO_TOKENが未設定です。ダウンロードしたパッケージは個別に再利用できます。')
    return GitHubModels(os.getenv('GATE_MODEL_REPOSITORY','UeEmon/AI-Gate-JP-Models'),token).publish(path)
