import csv
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import events
import registry_csv
import mail_delivery
from web import JobManager,create_app

def payload(rows,encoding='utf-8-sig'):
    stream=io.StringIO(newline='');writer=csv.writer(stream);writer.writerow(registry_csv.COLUMNS);writer.writerows(rows)
    return stream.getvalue().encode(encoding)

ROW=['品川','300','あ','1234','car','社用車','0','1']

class CSVTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);events.initialize(self.root)
    def tearDown(self):self.tmp.cleanup()
    def records(self):
        with events.connection(self.root) as db:return [dict(r) for r in db.execute('SELECT * FROM vehicles')]
    def test_preview_add_update_roundtrip(self):
        data=payload([ROW]);result=registry_csv.import_registry(self.root,data)
        self.assertEqual(result['added'],1);self.assertEqual(self.records(),[])
        registry_csv.import_registry(self.root,data,preview=False)
        original=self.records()[0]
        updated=ROW.copy();updated[5]='変更';updated[6]='true'
        self.assertEqual(registry_csv.import_registry(self.root,payload([updated]),preview=False)['skipped'],1)
        registry_csv.import_registry(self.root,payload([updated]),mode='update',preview=False)
        current=self.records()[0];self.assertEqual(current['id'],original['id']);self.assertEqual(current['watch'],1)
        self.assertEqual(current['label'],'変更')
        exported=registry_csv.export_registry(self.root);self.assertTrue(exported.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(registry_csv.parse_registry(exported)[0]['label'],'変更')
    def test_formula_labels_and_quoted_multiline_roundtrip(self):
        for label in ['=1+2','+SUM(A1)','@SUM(A1)',"'quoted",'名前,車両\n改行']:
            row=ROW.copy();row[5]=label
            registry_csv.import_registry(self.root,payload([row]),mode='update',preview=False)
            exported=registry_csv.export_registry(self.root)
            self.assertEqual(registry_csv.parse_registry(exported)[0]['label'],label)
            if label[0] in '=+@':self.assertIn("'"+label,exported.decode('utf-8-sig'))
    def test_cp932_and_bad_rows_leave_database_unchanged(self):
        self.assertEqual(registry_csv.parse_registry(payload([ROW],'cp932'))[0]['region'],'品川')
        invalid=ROW.copy();invalid[3]='9999';invalid[6]='yes'
        for data in [payload([ROW,invalid]),payload([ROW,ROW]),b'bad,columns\n',b'\x00',b'x'*(registry_csv.MAX_BYTES+1)]:
            with self.assertRaises(ValueError):registry_csv.import_registry(self.root,data,preview=False)
        self.assertEqual(self.records(),[])
    def test_plate_kana_and_ascii_serial_rules_apply_to_csv(self):
        for kana in ['ぁ','が','ぱ','し']:
            row=ROW.copy();row[2]=kana
            with self.subTest(kana=kana), self.assertRaises(ValueError):
                registry_csv.parse_registry(payload([row]))
        for serial in ['１２３４','12-34','12345']:
            row=ROW.copy();row[3]=serial
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                registry_csv.parse_registry(payload([row]))
    def test_csv_api_auth_csrf_preview_and_commit(self):
        manager=JobManager(self.root);app=create_app(manager=manager,password='secret');client=app.test_client();auth=('admin','secret')
        self.assertEqual(client.get('/api/vehicles/export.csv').status_code,401)
        client.get('/',auth=auth)
        with client.session_transaction() as s:headers={'X-CSRF-Token':s['csrf']}
        url='/api/vehicles/import.csv'
        self.assertEqual(client.post(url,auth=auth).status_code,403)
        def request(preview):return client.post(url,data={'file':(io.BytesIO(payload([ROW])),'vehicles.csv'),'preview':preview},headers=headers,auth=auth)
        self.assertEqual(request('1').json['added'],1);self.assertEqual(self.records(),[])
        self.assertEqual(request('0').status_code,200);self.assertEqual(len(self.records()),1)
        with client.get('/api/vehicles/export.csv',auth=auth) as response:
            self.assertIn('attachment',response.headers['Content-Disposition']);self.assertTrue(response.data.startswith(b'\xef\xbb\xbf'))
        manager.shutdown()

class SMTPTests(unittest.TestCase):
    @patch.dict(os.environ,{'GATE_EMAIL_FROM':'gate@example.test','GATE_SMTP_HOST':'mail.example.test','GATE_SMTP_USER':'gate','GATE_SMTP_PASSWORD':'test','GATE_SMTP_TLS':'starttls'})
    def test_smtp_tls_auth_and_unicode(self):
        with patch('mail_delivery.smtplib.SMTP') as factory:
            client=factory.return_value.__enter__.return_value;client.send_message.return_value={}
            result=mail_delivery.send_smtp('車両検知','品川 300 あ 1234',['operator@example.test'])
            client.starttls.assert_called_once();client.login.assert_called_once_with('gate','test')
            self.assertIn('品川',client.send_message.call_args.args[0].get_content());self.assertTrue(result['MessageId'])
    @patch.dict(os.environ,{'GATE_EMAIL_FROM':'gate@example.test','GATE_SMTP_HOST':'mail.example.test','GATE_SMTP_TLS':'ssl','GATE_SMTP_USER':''})
    def test_ssl_and_partial_refusal(self):
        with patch('mail_delivery.smtplib.SMTP_SSL') as factory:
            client=factory.return_value.__enter__.return_value;client.send_message.return_value={'bad@example.test':(550,b'refused')}
            with self.assertRaises(mail_delivery.smtplib.SMTPRecipientsRefused):mail_delivery.send_smtp('alert','test',['bad@example.test'])
            client.starttls.assert_not_called()
    @patch.dict(os.environ,{'GATE_EMAIL_BACKEND':'smtp','GATE_EMAIL_FROM':'gate@example.test','GATE_EMAIL_TO':'operator@example.test','GATE_S3_BUCKET':''})
    def test_dispatcher_smtp_without_aws(self):
        with tempfile.TemporaryDirectory() as root:
            events.initialize(root);identifier=events.evaluate(root,dict(id='a',run_id='run',vehicle_type='car',plate_candidates=[]),0)
            aws=Mock(side_effect=AssertionError('AWS must not be called'))
            with patch('mail_delivery.send_smtp',return_value={'MessageId':'smtp-1'}) as sender:
                events.Dispatcher(root,aws).tick();sender.assert_called_once();aws.assert_not_called()
            with events.connection(root) as db:self.assertEqual(db.execute('SELECT email_status FROM alerts WHERE id=?',(identifier,)).fetchone()[0],'sent')
