"""Read-only local gallery for UML sources rendered by an internal Kroki server."""
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DIAGRAMS = Path(os.environ.get('UML_SOURCE_DIR', '/diagrams'))
KROKI_URL = os.environ.get('KROKI_URL', 'http://kroki:8000').rstrip('/')
HEADING = re.compile(r'^## (\d+)\. (.+)$', re.M)
BLOCK = re.compile(r'^```mermaid\s*\n(.*?)^```\s*$', re.M | re.S)
MAX_SOURCE_BYTES = 100_000


def diagrams():
    """Extract only committed diagram sources from the fixed read-only directory."""
    markdown = (DIAGRAMS / 'UML.md').read_text(encoding='utf-8')
    headings = list(HEADING.finditer(markdown))
    result = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        block = BLOCK.search(markdown, heading.end(), end)
        if block:
            result[f'mermaid-{heading.group(1)}'] = ('mermaid', heading.group(2), block.group(1).strip())
    plantuml = (DIAGRAMS / 'deployment.puml').read_text(encoding='utf-8').strip()
    result['deployment'] = ('plantuml', 'UML配置図（PlantUML）', plantuml)
    if not result or any(len(item[2].encode('utf-8')) > MAX_SOURCE_BYTES for item in result.values()):
        raise ValueError('図の読み込みに失敗しました。')
    return result


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
        try:
            sources = diagrams()
        except (OSError, ValueError):
            self.respond(503, 'UML.md と deployment.puml を確認してください。'.encode())
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
<a id="source" href="#">ソースを見る</a><a id="download" href="#">SVGを保存</a>
<p id="status" role="status">図を読み込み中です。</p><div class="canvas"><img id="drawing" alt="選択したUML図"></div>
<script>
const select=document.getElementById('choose'), drawing=document.getElementById('drawing');
const status=document.getElementById('status');
function show(){{const id=encodeURIComponent(select.value);
  drawing.hidden=true; status.textContent='図を読み込み中です。';
  document.getElementById('source').href='/source/'+id;
  document.getElementById('download').href='/download/'+id+'.svg';
  drawing.src='/diagram/'+id+'.svg';
}}
drawing.onload=()=>{{drawing.hidden=false;status.textContent='表示しました。'}};
drawing.onerror=()=>{{status.textContent='描画に失敗しました。docker compose -f onprem/compose.uml.yaml logs kroki kroki-mermaid で確認してください。'}};
select.onchange=show;show();
</script></main></html>'''
            self.respond(200, body.encode('utf-8'))
            return
        source_match = re.fullmatch(r'/source/([a-z0-9-]+)', path)
        image_match = re.fullmatch(r'/(diagram|download)/([a-z0-9-]+)\.svg', path)
        key = source_match.group(1) if source_match else image_match.group(2) if image_match else None
        if key not in sources:
            self.respond(404, b'Not found', 'text/plain; charset=utf-8')
            return
        kind, title, source = sources[key]
        if source_match:
            body = f'<!doctype html><html lang="ja"><meta charset="utf-8"><title>{html.escape(title)}</title><h1>{html.escape(title)}</h1><p><a href="/">一覧へ</a></p><pre>{html.escape(source)}</pre></html>'
            self.respond(200, body.encode('utf-8'))
            return
        request = Request(f'{KROKI_URL}/{kind}/svg', data=source.encode('utf-8'),
                          headers={'Content-Type': 'text/plain; charset=utf-8', 'Accept': 'image/svg+xml'},
                          method='POST')
        try:
            with urlopen(request, timeout=30) as response:
                image = response.read(2_000_001)
                if len(image) > 2_000_000 or b'<svg' not in image[:1024]:
                    raise ValueError('描画結果がSVGではありません。')
            self.respond(200, image, 'image/svg+xml', attachment=f'{key}.svg' if image_match.group(1) == 'download' else None)
        except (HTTPError, URLError, TimeoutError, ValueError) as error:
            message = f'Krokiによる描画に失敗しました ({type(error).__name__})。コンテナのログを確認してください。'
            self.respond(502, message.encode('utf-8'), 'text/plain; charset=utf-8')


if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
