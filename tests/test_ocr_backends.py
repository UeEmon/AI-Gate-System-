import json
import os
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import numpy as np

from ocr_backends import FastALPRReader, LiplaPlateReader, PaddlePlateReader, make_readers, selected_backend


class Result:
    def __init__(self,text,score): self.json={'res':{'rec_text':text,'rec_score':score}}


class Recognition:
    def __init__(self,**kwargs): self.options=kwargs;self.calls=[]
    def predict(self,input,batch_size):
        self.calls.append(input.shape)
        return [Result('品川300' if len(self.calls)==1 else 'さ1234',.9)]


class OCRBackendTests(unittest.TestCase):
    def test_fast_alpr_reader_adapts_result(self):
        class OCR:
            text='品川330さ1234'; confidence=[.9, .8]
        class ALPRResult:
            ocr=OCR()
        model=Mock(return_value=[ALPRResult()])
        module=types.SimpleNamespace(ALPR=Mock(return_value=types.SimpleNamespace(predict=model)))
        with patch.dict('sys.modules', {'fast_alpr': module}):
            reader=FastALPRReader()
            items=reader.readtext(np.zeros((40,120,3), dtype=np.uint8))
        self.assertEqual(items[0][1], '品川330さ1234')
        self.assertAlmostEqual(items[0][2], .85)

    def test_fast_alpr_backend_is_selectable(self):
        module=types.SimpleNamespace(ALPR=Mock(return_value=Mock()))
        with patch.dict('sys.modules', {'fast_alpr': module}), patch.dict(os.environ, {'GATE_OCR_BACKEND':'fastalpr'}):
            readers=make_readers('/tmp/models', types.SimpleNamespace(), offline=True)
        self.assertEqual(readers[0][0], 'fast-alpr')

    def test_lipla_reader_adapts_japanese_fields(self):
        class LiplaResult:
            area='品川'; class_number='330'; kana='さ'; number=1234
            area_score=.95; class_number_score=.92; kana_score=.90; number_score=.88
        recognizer=Mock(return_value=[LiplaResult()])
        module=types.SimpleNamespace(Recognizer=Mock(return_value=recognizer))
        with patch.dict('sys.modules', {'lipla': module}):
            reader=LiplaPlateReader(cache_dir='/tmp/models')
            items=reader.readtext(np.zeros((40,120,3), dtype=np.uint8))
        self.assertEqual(items[0][1], '品川330さ1234')
        self.assertEqual(items[0][2], .88)

    def test_lipla_backend_is_selectable(self):
        class LiplaResult:
            area='品川'; class_number='330'; kana='さ'; number=1234
            area_score=class_number_score=kana_score=number_score=.9
        module=types.SimpleNamespace(Recognizer=Mock(return_value=Mock(return_value=[LiplaResult()])))
        with patch.dict('sys.modules', {'lipla': module}), patch.dict(os.environ, {'GATE_OCR_BACKEND':'lipla'}):
            readers=make_readers('/tmp/models', types.SimpleNamespace(), offline=True)
        self.assertEqual(readers[0][0], 'lipla-jp')

    def test_paddle_reader_splits_plate_into_two_rows(self):
        module=types.SimpleNamespace(TextRecognition=Recognition)
        with patch.dict('sys.modules',{'paddleocr':module}):
            reader=PaddlePlateReader()
        items=reader.readtext(np.zeros((100,200,3),np.uint8))
        self.assertEqual([item[1] for item in items],['品川300','さ1234'])
        self.assertEqual(reader.model.calls,[(45,200,3),(55,200,3)])

    def test_auto_backend_uses_benchmark_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            path=os.path.join(directory,'ocr-learning');os.mkdir(path)
            with open(os.path.join(path,'benchmark.json'),'w',encoding='utf-8') as stream:
                json.dump({'selected':'easyocr'},stream)
            with patch.dict(os.environ,{'GATE_OCR_BACKEND':'auto'}):
                self.assertEqual(selected_backend(directory),'easyocr')


if __name__=='__main__': unittest.main()
