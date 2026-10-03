import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from app import identify_observation, open_database, prepare_vehicle_identity, update_observation
from vehicle_identity import (BoundedIdentityExecutor, ChatGPTProvider, GeminiProvider, VehicleIdentityError,
                              VehicleIdentityService, parse_identity)


class VehicleIdentityTests(unittest.TestCase):
    def test_parse_identity_accepts_fenced_json_and_bounds_values(self):
        result = parse_identity('```json\n{"manufacturer":"Toyota","model":"Prius",'
                                '"confidence":4,"reason":"visible badge"}\n```')
        self.assertEqual(result['manufacturer'], 'Toyota')
        self.assertEqual(result['model'], 'Prius')
        self.assertEqual(result['confidence'], 1.0)

    def test_chatgpt_failure_falls_back_to_gemini(self):
        chatgpt = Mock(name='chatgpt', identify=Mock(side_effect=VehicleIdentityError('quota')))
        gemini = Mock(name='gemini', identify=Mock(return_value={
            'manufacturer': 'Toyota', 'model': 'Prius', 'confidence': .91, 'reason': 'shape'}))
        chatgpt.name, gemini.name = 'chatgpt', 'gemini'
        service = VehicleIdentityService((chatgpt, gemini))
        result = service.identify(b'jpeg')
        self.assertEqual(result['provider'], 'gemini')
        self.assertEqual(result['status'], 'identified')
        chatgpt.identify.assert_called_once_with(b'jpeg')
        gemini.identify.assert_called_once_with(b'jpeg')

    def test_chatgpt_success_does_not_call_gemini(self):
        primary = Mock()
        primary.name = 'chatgpt'
        primary.identify.return_value = parse_identity('{"manufacturer":"Honda","model":"Fit","confidence":0.9}')
        secondary = Mock()
        result = VehicleIdentityService((primary, secondary)).identify(b'jpeg')
        self.assertEqual(result['provider'], 'chatgpt')
        secondary.identify.assert_not_called()

    def test_missing_or_invalid_response_fields_trigger_fallback(self):
        for response in ('{}', '[]', '{"manufacturer":"Honda","model":"Fit","confidence":NaN}',
                         '{"manufacturer":"Honda","model":"Fit","confidence":"bad"}'):
            with self.subTest(response=response):
                primary = Mock()
                primary.name = 'chatgpt'
                primary.identify.side_effect = lambda _image: parse_identity(response)
                secondary = Mock()
                secondary.name = 'gemini'
                secondary.identify.return_value = parse_identity(
                    '{"manufacturer":"Toyota","model":"Prius","confidence":0.9}')
                self.assertEqual(VehicleIdentityService((primary, secondary)).identify(b'jpeg')['provider'], 'gemini')

    def test_no_credentials_and_offline_mode_never_send_images(self):
        with patch.dict(os.environ, {}, clear=True):
            service = VehicleIdentityService.from_environment()
            self.assertFalse(service.enabled)
            self.assertEqual(service.identify(b'jpeg')['status'], 'disabled')
        with patch.dict(os.environ, {'OPENAI_API_KEY':'test-primary', 'GEMINI_API_KEY':'test-secondary'}, clear=True):
            online = VehicleIdentityService.from_environment()
            self.assertEqual([p.name for p in online.providers], ['chatgpt', 'gemini'])
            self.assertFalse(VehicleIdentityService.from_environment(offline=True).enabled)

    def test_http_and_timeout_failures_use_gemini(self):
        for error in (HTTPError('https://api.openai.com/v1/responses',429,'quota',{},None),
                      HTTPError('https://api.openai.com/v1/responses',401,'auth',{},None),
                      URLError('offline'), TimeoutError('timeout')):
            with self.subTest(error=type(error).__name__):
                secondary = Mock()
                secondary.name = 'gemini'
                secondary.identify.return_value = parse_identity(
                    '{"manufacturer":"Toyota","model":"Prius","confidence":0.9}')
                with patch('vehicle_identity.urlopen', side_effect=error):
                    result = VehicleIdentityService((ChatGPTProvider('test-key'),secondary)).identify(b'jpeg')
                self.assertEqual(result['provider'],'gemini')

    def test_all_provider_failures_are_reported_without_secrets(self):
        primary = Mock()
        primary.name = 'chatgpt'
        primary.identify.side_effect = VehicleIdentityError('secret-value')
        result = VehicleIdentityService((primary,)).identify(b'jpeg')
        self.assertEqual(result['status'], 'unavailable')
        self.assertNotIn('secret-value', json.dumps(result))

    def test_chatgpt_request_contains_image_and_json_prompt(self):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=None)
        response.read.return_value = json.dumps({'output_text': '{"manufacturer":"Honda",'
                                                  '"model":"Fit","confidence":0.8}'}).encode()
        captured = {}

        def request(request, timeout):
            captured['url'] = request.full_url
            captured['body'] = json.loads(request.data)
            captured['timeout'] = timeout
            return response

        with patch('vehicle_identity.urlopen', side_effect=request):
            result = ChatGPTProvider('secret', timeout=7).identify(b'jpeg')
        self.assertEqual(result['model'], 'Fit')
        self.assertEqual(captured['url'], 'https://api.openai.com/v1/responses')
        self.assertEqual(captured['timeout'], 7)
        content = captured['body']['input'][0]['content']
        self.assertEqual(content[0]['type'], 'input_text')
        self.assertTrue(content[1]['image_url'].startswith('data:image/jpeg;base64,'))
        self.assertFalse(captured['body']['store'])

    def test_gemini_request_and_response_support_non_text_and_thinking_parts(self):
        payload = {'candidates':[{'content':{'parts':[
            {'text':'private reasoning', 'thought': True}, {'inlineData': {}},
            {'text':'{"manufacturer":"Honda","model":"Odyssey","confidence":0.91}'}]}}]}
        with patch('vehicle_identity._http_json', return_value=payload) as http:
            result = GeminiProvider('test-key').identify(b'jpeg')
        self.assertEqual(result['model'], 'Odyssey')
        url, body, headers, timeout = http.call_args.args
        self.assertTrue(url.endswith(':generateContent'))
        self.assertEqual(headers['x-goog-api-key'], 'test-key')
        self.assertEqual(body['contents'][0]['parts'][0]['inline_data']['mime_type'], 'image/jpeg')

    def test_first_eligible_frame_schedules_and_following_frames_do_not_duplicate(self):
        service = VehicleIdentityService((Mock(),))
        previous = {'result_eligible':False, 'vehicle_identity':{'status':'not_requested'}}
        record = {'result_eligible':True, 'vehicle_identity':service.initial_result()}
        self.assertTrue(prepare_vehicle_identity(record, previous, service))
        next_frame = {'result_eligible':True, 'vehicle_identity':service.initial_result()}
        self.assertFalse(prepare_vehicle_identity(next_frame, record, service))

    def test_better_ocr_frame_keeps_completed_identity_in_database(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with open_database(root / 'gate.db') as db:
                record = dict(id='obs', processed_at='2026-10-01', run_id='run', frame_index=1,
                              media_ms=0, vehicle_type='car', confidence=.9, image_path=None,
                              vehicle_identity={'status':'pending'})
                db.execute('INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)',
                           ('obs','2026-10-01','run',1,0,'car',.9,None,json.dumps(record)))
            service = Mock()
            service.identify.return_value = dict(status='identified', provider='chatgpt',
                manufacturer='Honda', model='Odyssey', confidence=.91)
            identify_observation(root,'obs',b'jpeg',service)
            record.update(frame_index=2, confidence=.99)
            with open_database(root / 'gate.db') as db:
                update_observation(db,record)
                saved=json.loads(db.execute('SELECT details_json FROM observations').fetchone()[0])
            self.assertEqual(saved['frame_index'],2)
            self.assertEqual(saved['confidence'],.99)
            self.assertEqual(saved['vehicle_identity']['model'],'Odyssey')

    def test_bounded_background_queue_rejects_excess_and_drains_at_normal_completion(self):
        entered, release = threading.Event(), threading.Event()
        completed = []
        def work(value):
            entered.set()
            release.wait(5)
            completed.append(value)
        executor = BoundedIdentityExecutor(max_pending=2)
        try:
            first = executor.submit(work,1)
            self.assertTrue(entered.wait(2))
            second = executor.submit(work,2)
            self.assertIsNone(executor.submit(work,3))
            release.set()
            executor.shutdown(wait=True)
            self.assertEqual(completed,[1,2])
            self.assertTrue(first.done() and second.done())
        finally:
            release.set()
            executor.shutdown(wait=True)

    def test_background_result_is_merged_without_replacing_other_record_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with open_database(root / 'gate.db') as db:
                db.execute('''INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)''',
                           ('obs', '2026-10-01', 'run', 1, 0, 'car', .9, None,
                            json.dumps({'id': 'obs', 'plate_candidates': [], 'confidence': .9}, ensure_ascii=False)))
            service = Mock()
            service.identify.return_value = {'status': 'identified', 'provider': 'chatgpt',
                                              'manufacturer': 'Nissan', 'model': 'Note', 'confidence': .88}
            identify_observation(root, 'obs', b'jpeg', service)
            with open_database(root / 'gate.db') as db:
                record = json.loads(db.execute('SELECT details_json FROM observations WHERE id="obs"').fetchone()[0])
            self.assertEqual(record['plate_candidates'], [])
            self.assertEqual(record['vehicle_identity']['model'], 'Note')
            self.assertIn('elapsed_ms', record['vehicle_identity'])


if __name__ == '__main__':
    unittest.main()
