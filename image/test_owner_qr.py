"""Offline optical round trip on macOS. Uses random test keys, no device or UI."""
import importlib.util
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('prepare_card', HERE/'prepare-card.py')
card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(card)

# Compare inside the process and return only the result, never the decoded payload.
DECODE = """ObjC.import('Foundation'); ObjC.import('CoreImage');
function run(argv) {
    const file = $.NSURL.fileURLWithPath($(argv[0]));
    const image = $.CIImage.imageWithContentsOfURL(file);
    const opts = $.NSMutableDictionary.alloc.init;
    opts.setObjectForKey($(true), $.kCIContextUseSoftwareRenderer);
    const ctx = $.CIContext.contextWithOptions(opts);
    const detector = $.CIDetector.detectorOfTypeContextOptions($.CIDetectorTypeQRCode, ctx, $({}));
    const found = detector.featuresInImage(image);
    if (Number(found.count) !== 1) throw new Error('Expected exactly one QR code');
    const text = ObjC.unwrap(found.objectAtIndex(0).messageString);
    const raw = $.NSString.stringWithContentsOfFileEncodingError($(argv[1]), $.NSASCIIStringEncoding, null);
    return text === 'duckbase-owner:v1:' + ObjC.unwrap(raw).trim() ? 'match' : 'mismatch';
}"""


@unittest.skipUnless(sys.platform == 'darwin', 'macOS CoreImage')
class OwnerQRTests(unittest.TestCase):
    def test_system_decoder_reads_new_owner_key_from_pixels(self):
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary)
            key = receipt/'duck-home.key'
            key.touch(mode=0o600)
            key.write_text(secrets.token_hex(32)+'\n')
            png = card.create_owner_qr(receipt)
            self.assertEqual(stat.S_IMODE(png.stat().st_mode), 0o600)
            def read_match():
                result = subprocess.run(['/usr/bin/osascript', '-l', 'JavaScript', '-e', DECODE, str(png), str(key)],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()
            self.assertEqual(read_match(), 'match')
            key.write_text(secrets.token_hex(32)+'\n')
            self.assertEqual(read_match(), 'mismatch')


if __name__ == '__main__':
    unittest.main()
