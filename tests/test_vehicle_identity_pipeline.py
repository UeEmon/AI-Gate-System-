"""Exercise actual frame I/O and database updates with external inference mocked."""
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

import cv2
import numpy as np

import app
from vehicle_identity import VehicleIdentityService


class VehicleIdentityPipelineTests(unittest.TestCase):
    def test_threshold_transition_and_best_frame_keep_one_completed_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'cars.avi'
            writer = cv2.VideoWriter(str(source), cv2.VideoWriter_fourcc(*'MJPG'), 10, (160,100))
            self.assertTrue(writer.isOpened())
            for _ in range(4):
                writer.write(np.full((100,160,3),120,dtype=np.uint8))
            writer.release()
            box = types.SimpleNamespace(cls=np.array(0),conf=np.array(.8),
                                        xyxy=np.array([[0,0,160,100]]))
            detector = Mock()
            detector.predict.return_value = [types.SimpleNamespace(names={0:'car'},boxes=[box])]
            plates = [[dict(text='品川300あ1234',confidence=confidence,
                            fields=dict(region='品川',category='300',kana='あ',serial='1234'),
                            bbox_in_vehicle=[20,60,120,90])]
                      for confidence in (.85,.95,.97,.99)]
            provider = Mock()
            provider.name = 'chatgpt'
            provider.identify.return_value = dict(manufacturer='Honda', model='Odyssey',
                                                 confidence=.92,reason='badge')
            service = VehicleIdentityService((provider,))
            argv = ['app.py','--source',str(source),'--source-kind','file','--every','1',
                    '--output',str(root),'--vehicle-threshold','.6','--ocr-threshold','.9',
                    '--save-images']
            with patch.dict(sys.modules,{'ultralytics':types.SimpleNamespace(YOLO=Mock())}), \
                 patch.dict(os.environ,{},clear=True), patch.object(sys,'argv',argv), \
                 patch.object(app,'initialize_models',return_value=(detector,None,[])), \
                 patch.object(app,'read_plate',side_effect=plates), \
                 patch.object(VehicleIdentityService,'from_environment',return_value=service), \
                 patch('builtins.print'):
                app.main()
            with app.open_database(root/'gate.db') as db:
                rows=db.execute('SELECT details_json FROM observations').fetchall()
            self.assertEqual(len(rows),1)
            record=json.loads(rows[0][0])
            self.assertEqual(record['frame_index'],3)
            self.assertEqual(record['plate_candidates'][0]['confidence'],.99)
            self.assertEqual(record['vehicle_identity']['model'],'Odyssey')
            self.assertTrue(Path(record['image_path']).is_file())
            provider.identify.assert_called_once()


if __name__ == '__main__':
    unittest.main()
