"""Read-only local gallery using Pyreverse and the Java PlantUML renderer."""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit
import threading
import generator


DIAGRAMS = Path(os.environ.get('UML_SOURCE_DIR', '/diagrams'))
PLANTUML_JAR = os.environ.get('PLANTUML_JAR', '/opt/plantuml.jar')
RENDER_LOCK = threading.Lock()
MAX_SOURCE_BYTES = 100_000
TITLES = {
    'classes': '主要クラスと依存関係',
    'recognition': '認識から通知まで',
    'learning': 'OCR学習と評価',
    'job-state': 'ジョブ状態',
    'data-model': '保存データの論理関係',
    'containers': 'コンテナ構成',
    'deployment': 'UML配置図',
}


def diagrams():
    """Load authored PlantUML and list lazily generated Pyreverse diagrams."""
    result = {key: ('plantuml', title, (DIAGRAMS / f'{key}.puml').read_text(encoding='utf-8').strip())
              for key, title in TITLES.items()}
    result.update({key: ('plantuml', title, None) for key, title in generator.titles().items()})
    if any(item[2] is not None and len(item[2].encode('utf-8')) > MAX_SOURCE_BYTES for item in result.values()):
        raise ValueError('図の読み込みに失敗しました。')
    return result


def render_svg(source):
    """Render with bounded Java memory and only one active renderer."""
    with RENDER_LOCK:
        result = subprocess.run(
            ['java', '-Xmx256m', '-Djava.awt.headless=true',
             '-DPLANTUML_SECURITY_PROFILE=SANDBOX', '-jar', PLANTUML_JAR,
             '-charset', 'UTF-8', '-pipe', '-tsvg', '-timeout', '45'],
            input=source.encode('utf-8'), capture_output=True, timeout=60, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace')[-2000:]
                           or f'PlantUML終了コード: {result.returncode}')
    image = result.stdout
    if len(image) > 2_000_000 or b'<svg' not in image[:1024]:
        raise ValueError('描画結果がSVGではありません。')
    return image


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, body, content_type='text/html; charset=utf-8', attachment=None):
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('X-Frame-Options', 'DENY')
        self.send_header('Content-Security-Policy', "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'")
        if attachment:
            self.send_header('Content-Disposition', f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/healthz':
            self.respond(200, b'ok', 'text/plain; charset=utf-8')
            return
        if path == '/regenerate':
            try:
                for group in generator.GROUPS:
                    generator.source(group, 'classes', force=True)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                self.log_error('Pyreverse: %s', error)
                self.respond(503, html.escape(str(error)).encode('utf-8'))
                return
            self.send_response(303)
            self.send_header('Location', '/')
            self.end_headers()
            return
        try:
            sources = diagrams()
        except (OSError, ValueError):
            self.respond(503, 'docs内のPlantUMLファイルを確認してください。'.encode())
            return
        if path == '/':
            options = ''.join(f'<option value="{html.escape(key)}">{html.escape(title)}</option>'
                              for key, (_, title, _) in sources.items())
            body = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI Gate System UML</title><style>
body{{font:16px system-ui,sans-serif;margin:0;background:#f5f7fa;color:#202838}}
header{{background:#18344e;color:white;padding:1rem 2rem}}
main{{max-width:1320px;margin:auto;padding:1.5rem}}
select,a{{font:inherit;margin:.4rem;padding:.4rem}}a{{color:#005a90}}
.canvas{{background:white;border:1px solid #d7dde4;overflow:auto;min-height:200px;padding:1rem}}
.canvas img{{max-width:none;height:auto}}
pre{{background:#ecf0f4;white-space:pre-wrap;overflow-wrap:anywhere;padding:1rem}}
</style><header><h1>AI Gate System UML</h1></header><main>
<label for="choose">図を選択</label><select id="choose">{options}</select>
<a id="source" href="#">ソースを見る</a><a id="puml" href="#">PlantUMLを保存</a><a id="download" href="#">SVGを保存</a><a href="/regenerate">自動図を再生成</a>
<p id="status" role="status">図を読み込み中です。</p><div class="canvas"><img id="drawing" alt="選択したUML図"></div>
<script>
const select=document.getElementById('choose'), drawing=document.getElementById('drawing');
const status=document.getElementById('status');
function show(){{const id=encodeURIComponent(select.value);
  drawing.hidden=true; status.textContent='図を読み込み中です。';
  document.getElementById('source').href='/source/'+id;
  document.getElementById('puml').href='/download/'+id+'.puml';
  document.getElementById('download').href='/download/'+id+'.svg';
  drawing.src='/diagram/'+id+'.svg';
}}
drawing.onload=()=>{{drawing.hidden=false;status.textContent='表示しました。'}};
drawing.onerror=()=>{{status.textContent='描画に失敗しました。docker compose -f onprem/compose.uml.yaml logs uml で確認してください。'}};
select.onchange=show;show();
</script></main></html>'''
            self.respond(200, body.encode('utf-8'))
            return
        source_match = re.fullmatch(r'/source/([a-z0-9-]+)', path)
        puml_match = re.fullmatch(r'/download/([a-z0-9-]+)\.puml', path)
        image_match = re.fullmatch(r'/(diagram|download)/([a-z0-9-]+)\.svg', path)
        key = (source_match.group(1) if source_match else puml_match.group(1) if puml_match
               else image_match.group(2) if image_match else None)
        if key not in sources:
            self.respond(404, b'Not found', 'text/plain; charset=utf-8')
            return
        kind, title, source = sources[key]
        if source is None:
            group, subtype = key.removeprefix('auto-').rsplit('-', 1)
            try:
                source = generator.source(group, subtype)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
                self.log_error('Pyreverse: %s', error)
                self.respond(503, html.escape(str(error)).encode('utf-8'))
                return
        if len(source.encode('utf-8')) > MAX_SOURCE_BYTES:
            self.respond(503, '図が大きすぎます。'.encode('utf-8'))
            return
        if puml_match:
            self.respond(200, source.encode('utf-8'), 'text/plain; charset=utf-8', attachment=f'{key}.puml')
            return
        if source_match:
            body = f'<!doctype html><html lang="ja"><meta charset="utf-8"><title>{html.escape(title)}</title><h1>{html.escape(title)}</h1><p><a href="/">一覧へ</a></p><pre>{html.escape(source)}</pre></html>'
            self.respond(200, body.encode('utf-8'))
            return
        try:
            image = render_svg(source)
            self.respond(200, image, 'image/svg+xml', attachment=f'{key}.svg' if image_match.group(1) == 'download' else None)
        except (OSError, RuntimeError, subprocess.TimeoutExpired, ValueError) as error:
            self.log_error('PlantUML: %s', error)
            message = f'PlantUMLによる描画に失敗しました ({type(error).__name__})。コンテナのログを確認してください。'
            self.respond(502, message.encode('utf-8'), 'text/plain; charset=utf-8')


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
