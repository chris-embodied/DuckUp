import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from motion_runtime import BASE, BUILD, MARKER, V260, read_runtime
from prepare_voice import prepare

BOARD = Path(__file__).resolve().parents[1] / 'robot/base'


class MotionRuntimeTests(unittest.TestCase):
    def test_unexpected_release_rejected_before_import(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root/'BUILD_MANIFEST.json').write_text(json.dumps({'console_version':'1.0.167','service_build':BUILD}))
            with self.assertRaisesRegex(ValueError,'V260'):
                read_runtime(root, BOARD)

    def test_retry_bank_preparation_and_keep_installed_emotions(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bank = root/'bank'
            bank.mkdir()
            tag = root/'emotion-sounds'/'curious'
            tag.mkdir(parents=True)
            (tag/'curious.wav').write_bytes(b'example-wave')
            with patch('prepare_voice.subprocess.run', side_effect=OSError('unavailable')):
                with self.assertRaises(OSError):
                    prepare(root,bank,'serial')
            self.assertFalse((bank/'.duck-base-version').exists())
            with patch('prepare_voice.subprocess.run') as generator:
                prepare(root,bank,'serial')
                prepare(root,bank,'serial')
                generator.assert_called_once()
                self.assertEqual(generator.call_args.args[0][1:4], ['ensure-bank','--dir',str(bank)])
                self.assertEqual((bank/'curious'/'curious.wav').read_bytes(),b'example-wave')

    @unittest.skipUnless(os.getenv('DUCK_V260_CONSOLE'), 'Set DUCK_V260_CONSOLE to your local V260 release')
    def test_local_release_and_conditional_robot_service(self):
        files = read_runtime(Path(os.environ['DUCK_V260_CONSOLE']), BOARD)
        self.assertEqual(files[BASE+'DUCKMOT.ID'][0], MARKER)
        self.assertEqual(files[BASE+'robotd'][1], 0o755)
        self.assertEqual(len([path for path in files if path.endswith('/policy.onnx')]), 8)
        self.assertIn(V260+'emotion-sounds/curious/curious.wav', files)
        self.assertIn(V260+'onnxruntime/libonnxruntime.so.1.28.0', files)
        self.assertNotIn('/etc/duck-base/home.key', files)
        self.assertNotIn('/etc/duck-base/calibration.toml', files)
        unit = (BOARD/'duck-home.service').read_text()
        self.assertIn('ConditionPathExists=/etc/duck-base/home.key', unit)
        self.assertIn('After=duck-provision.service', unit)
        self.assertIn('Before=duck-base.service', unit)


if __name__ == '__main__':
    unittest.main()
