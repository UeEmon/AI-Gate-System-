"""Consecutive plate grouping preserves the highest OCR confidence."""
import unittest

from app import ConsecutivePlateBest


def record(number, score, bbox=(0, 0, 100, 80)):
    return dict(id=str(number), bbox=list(bbox), plate_candidates=[dict(
        fields=dict(region='品川', category='300', kana='あ', serial='1234'),
        confidence=score)])


class ConsecutivePlateBestTests(unittest.TestCase):
    def test_keeps_best_even_after_weaker_intermediate_frame(self):
        best = ConsecutivePlateBest()
        first = record(1, .7)
        self.assertEqual(best.select([first]), [(first, None)])
        self.assertEqual(best.select([record(2, .5)]), [(None, None)])
        third = record(3, .9)
        self.assertEqual(best.select([third]), [(third, first)])
        self.assertEqual(best.select([record(4, .8)]), [(None, None)])

    def test_separated_vehicles_and_gaps_create_new_records(self):
        best = ConsecutivePlateBest()
        first = record(1, .9)
        best.select([first])
        distant = record(2, .7, (500, 500, 600, 580))
        self.assertEqual(best.select([distant]), [(distant, None)])
        best.select([])
        another = record(3, .6)
        self.assertEqual(best.select([another]), [(another, None)])


if __name__ == '__main__':
    unittest.main()
