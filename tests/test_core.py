import tempfile
import unittest
from pathlib import Path
import json
from app import parse_plate, open_database, save_observation


class CoreTests(unittest.TestCase):
    def test_two_line_plate(self):
        self.assertEqual(parse_plate('品川 ３３０\nさ １２－３４'),
                         dict(region='品川', category='330', kana='さ', serial='1234'))

    def test_alphanumeric_category(self):
        self.assertEqual(parse_plate('横浜30Aあ12-34')['category'], '30A')

    def test_short_serial(self):
        self.assertEqual(parse_plate('千葉500あ・・12')['serial'], '12')

    def test_multi_character_local_plate_regions(self):
        self.assertEqual(parse_plate('富士山580さ12-34')['region'], '富士山')
        self.assertEqual(parse_plate('伊勢志摩500あ5678')['region'], '伊勢志摩')

    def test_incomplete_and_unrelated_text(self):
        for text in ['TOYOTA', '12-34', '', '品川330さ12345', '品川330さ・・・',
                     '品川330ぁ1234', '品川330が1234', '品川330し1234']:
            with self.subTest(text=text):
                self.assertIsNone(parse_plate(text))

    def test_database_append_and_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'gate.db'
            for identifier in ['a', 'b']:
                db = open_database(path)
                record = dict(id=identifier, processed_at='2026-09-16T00:00:00+00:00',
                              run_id='run', frame_index=0, media_ms=None,
                              vehicle_type='car', confidence=0.8, image_path=None,
                              text="品川330さ12-34 ' quoted")
                save_observation(db, record)
                db.close()
            db = open_database(path)
            rows = db.execute('SELECT details_json FROM observations').fetchall()
            self.assertEqual(len(rows), 2)
            self.assertEqual(json.loads(rows[0][0])['text'], record['text'])
            db.close()


if __name__ == '__main__':
    unittest.main()
