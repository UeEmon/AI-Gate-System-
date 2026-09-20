import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from runtime_profile import measure, model_choices


class ProfileTests(unittest.TestCase):
    def test_measurement_produces_supported_defaults(self):
        result = measure()
        self.assertIn(result['imgsz'], (640, 960))
        self.assertGreater(result['score'], 0)
        self.assertGreater(result['cpu_count'], 0)

    def test_local_models_and_symlink_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            custom = Path(folder) / 'vehicle-custom.pt'
            custom.touch()
            (Path(folder) / 'outside.pt').symlink_to('/etc/passwd')
            result = model_choices(str(Path(folder) / 'yolo26s.pt'))
            self.assertEqual(result['vehicle-custom.pt'], str(custom))
            self.assertNotIn('outside.pt', result)

    def test_low_cpu_uses_light_defaults(self):
        with patch('os.sched_getaffinity', return_value={0}):
            result = measure()
        self.assertEqual(result['model'], 'yolo26n.pt')
        self.assertEqual(result['cameras'], 1)
