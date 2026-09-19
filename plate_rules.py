"""Canonical Japanese registration plate character rules."""
import re

# Union of ordinary business, private and rental hiragana marks. Small kana,
# voiced/semi-voiced kana and the unused お・し・へ・ん are intentionally absent.
PLATE_KANA = 'あいうえかきくけこさすせそたちつてとなにぬねのはひふほまみむめもやゆよらりるろわれを'
KANA_PATTERN = f'[{re.escape(PLATE_KANA)}]'
SERIAL_PATTERN = r'[0-9]{1,4}'
VEHICLE_RESULT_CONFIDENCE = 0.80
OCR_RESULT_CONFIDENCE = 0.70


def valid_kana(value):
    return re.fullmatch(KANA_PATTERN, value) is not None


def valid_serial(value):
    return re.fullmatch(SERIAL_PATTERN, value) is not None and int(value) > 0
