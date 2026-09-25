import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from plate_benchmark_web import create_app


class PlateWebTests(unittest.TestCase):
    def test_upload_results_and_access_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = create_app(tmp, tmp, password='test-password')
            app.testing = True
            client = app.test_client()
            auth = {'Authorization': 'Basic YWRtaW46dGVzdC1wYXNzd29yZA=='}
            self.assertEqual(client.get('/').status_code, 401)
            self.assertEqual(client.get('/healthz').status_code, 200)
            self.assertEqual(client.get('/', headers=auth).status_code, 200)
            self.assertEqual(client.post('/run', headers=auth).status_code, 403)
            with client.session_transaction() as session:
                csrf = session['csrf']

            def launch(command, **kwargs):
                self.assertNotIn('--model', command)
                output = Path(command[command.index('--output')+1]) / 'result'
                output.mkdir(parents=True)
                summary = dict(frames=1, inference_fps=10, inference_p95_ms=100,
                               end_to_end_fps=9, detected_frames=1, text_read_frames=1,
                               mean_stage_ms=dict(plate_detection_ms=20,rectification_ms=10,ocr_ms=70))
                (output/'summary.json').write_text(json.dumps(summary))
                frame = dict(frame_index=0, inference_ms=100, candidates=[dict(
                    text='<script>alert(1)</script>', bbox_in_frame=[0,0,10,10], confidence=.9)])
                (output/'frames.jsonl').write_text(json.dumps(frame)+'\n')
                (output/'00000000.jpg').write_bytes(b'image')
                return Mock(poll=Mock(return_value=0))

            with patch('plate_benchmark_web.subprocess.Popen', side_effect=launch) as process:
                response = client.post('/run', headers=auth, data=dict(
                    csrf=csrf, source=(io.BytesIO(b'fake'), 'plate.jpg'), ocr='paddle'))
                self.assertEqual(response.status_code, 302)
                process.assert_called_once()
            result = client.get(response.location, headers=auth)
            self.assertEqual(result.status_code, 200)
            self.assertIn(b'&lt;script&gt;', result.data)
            image = response.location+'/files/result/00000000.jpg'
            self.assertEqual(client.get(image).status_code, 401)
            with client.get(image,headers=auth) as image_response:
                self.assertEqual(image_response.status_code, 200)
            self.assertEqual(client.get(response.location+'/files/../../input.jpg',headers=auth).status_code, 404)
