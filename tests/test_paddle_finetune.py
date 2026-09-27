from pathlib import Path
import unittest

from paddle_finetune import recognition_options


class PaddleFinetuneOptionsTests(unittest.TestCase):
    def test_multiscale_sampler_and_both_loaders_are_single_process(self):
        settings = dict(option.split('=', 1) for option in
                        recognition_options(Path('/models/base.pdparams'),
                                            Path('/data/weights'), 20, Path('/data/rec')))
        self.assertEqual(settings['Train.sampler.first_bs'], '1')
        self.assertEqual(settings['Train.sampler.fix_bs'], 'True')
        self.assertEqual(settings['Train.loader.batch_size_per_card'], '1')
        self.assertEqual(settings['Train.loader.num_workers'], '0')
        self.assertEqual(settings['Eval.loader.batch_size_per_card'], '1')
        self.assertEqual(settings['Eval.loader.num_workers'], '0')


if __name__ == '__main__':
    unittest.main()
