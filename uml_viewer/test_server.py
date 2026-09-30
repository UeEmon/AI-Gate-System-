"""Exercise the gallery's source mapping and HTTP rendering boundary."""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import urlopen

import server


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, _limit):
        return self.body


@contextmanager
def website():
    http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{http.server_port}'
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


class UmlViewerTests(unittest.TestCase):
    def setUp(self):
        self.diagram_dir = Path(__file__).resolve().parents[1] / 'docs'
        self.patch_dir = patch.object(server, 'DIAGRAMS', self.diagram_dir)
        self.patch_dir.start()
        self.addCleanup(self.patch_dir.stop)

    def test_extracts_every_markdown_diagram_and_plantuml(self):
        diagrams = server.diagrams()
        self.assertEqual(len(diagrams), 7)
        self.assertEqual(diagrams['mermaid-1'][0], 'mermaid')
        self.assertIn('classDiagram', diagrams['mermaid-1'][2])
        self.assertEqual(diagrams['deployment'][0], 'plantuml')

    def test_source_escapes_markup_and_rejects_unknown_paths(self):
        with website() as base:
            with urlopen(base + '/source/mermaid-1') as response:
                self.assertIn(b'classDiagram', response.read())
            with self.assertRaises(HTTPError) as missing:
                urlopen(base + '/source/../../etc/passwd')
            self.assertEqual(missing.exception.code, 404)

    def test_render_uses_local_kroki_and_returns_svg_attachment(self):
        def render(request, timeout):
            self.assertEqual(request.full_url, 'http://kroki:8000/plantuml/svg')
            self.assertIn(b'@startuml', request.data)
            self.assertEqual(timeout, 30)
            return Response(b'<svg xmlns="http://www.w3.org/2000/svg"></svg>')

        with website() as base, patch.object(server, 'urlopen', side_effect=render):
            with urlopen(base + '/download/deployment.svg') as response:
                self.assertEqual(response.headers['Content-Type'], 'image/svg+xml')
                self.assertIn('attachment', response.headers['Content-Disposition'])
                self.assertIn(b'<svg', response.read())


if __name__ == '__main__':
    unittest.main()
