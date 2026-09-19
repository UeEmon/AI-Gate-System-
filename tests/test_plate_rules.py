import unittest

import events
from plate_rules import PLATE_KANA, valid_kana, valid_serial


class PlateRuleTests(unittest.TestCase):
    def test_allowed_plate_hiragana(self):
        for kana in PLATE_KANA:
            with self.subTest(kana=kana):
                self.assertTrue(valid_kana(kana))
                self.assertEqual(events.plate_key(dict(
                    region='品川', category='300', kana=kana, serial='1234')),
                    f'品川|300|{kana}|1234')

    def test_special_and_unused_hiragana_are_rejected(self):
        for kana in ['ぁ','ぃ','ぅ','ぇ','ぉ','っ','ゃ','ゅ','ょ','ゎ',
                     'が','ぎ','ぐ','げ','ご','ざ','じ','ず','ぜ','ぞ',
                     'だ','ぢ','づ','で','ど','ば','び','ぶ','べ','ぼ',
                     'ぱ','ぴ','ぷ','ぺ','ぽ','お','し','へ','ん']:
            with self.subTest(kana=kana):
                self.assertFalse(valid_kana(kana))
                with self.assertRaisesRegex(ValueError, '通常文字'):
                    events.plate_key(dict(region='品川', category='300', kana=kana, serial='1234'))

    def test_serial_is_ascii_one_to_four_digits(self):
        for serial in ['1','12','123','1234','0001']:
            with self.subTest(serial=serial):
                self.assertTrue(valid_serial(serial))
        for serial in ['','0','0000','12345','１２３４','12-34','12 34','1.2']:
            with self.subTest(serial=serial):
                self.assertFalse(valid_serial(serial))

