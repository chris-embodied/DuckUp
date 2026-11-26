"""Exercise the Mac card setup and actual first-boot importer using temporary directories."""
import base64
from functools import partial
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'image'), str(ROOT/'robot/base')]
import check_calibration as calibration
import provision

spec = importlib.util.spec_from_file_location('prepare_card', ROOT/'image/prepare-card.py')
prepare_card = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare_card)


def synthetic_calibration():
    # Synthetic neutral pose for a file round trip only.
    lines = ['# migration test fixture', 'motion_enabled = true','imu_mount_verified = true',
             'imu_mount_quat = [1, 0, 0, 0]']
    for i, name in calibration.NAMES.items():
        lines.extend(['[[joints]]',f'id = {i}',f'name = "{name}"','calibrated = true',
                      'direction = 1','min_rad = -0.1','max_rad = 0.15','zero_raw = 2000'])
    return ('\n'.join(lines)+'\n').encode()


class CardMigrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.tmp = Path(temporary.name)
        self.config = self.tmp/'config'
        self.config.mkdir()
        (self.config/'DUCKBASE.ID').write_bytes(b'duck-base-zero3w-v1\n')
        (self.config/'DUCKMOT.ID').write_bytes(calibration.MOTION_MARKER)
        self.cal = self.tmp/'backup.toml'
        self.cal.write_bytes(synthetic_calibration())
        self.pub = self.tmp/'existing.pub'
        self.pub.write_text('ssh-ed25519 ZmFrZQ== fixture\n')
        self.receipt = self.tmp/'private'
        self.dest = self.tmp/'etc/duck-base'
        self.marker = self.tmp/'opt/duck-base/DUCKMOT.ID'
        self.marker.parent.mkdir(parents=True)
        self.marker.write_bytes(calibration.MOTION_MARKER)
        self.run_os = Mock()

    def prepare(self, *, with_cal=True):
        return prepare_card.prepare(self.config, 'home=家', 'test-passphrase',
                                    self.pub, self.cal if with_cal else None, self.receipt)

    def boot(self):
        provision.import_card(self.config, self.dest, self.marker, os.getgid(),
                              network=partial(provision.configure_network, root=self.tmp, run=self.run_os))

    def test_new_card_to_first_boot_and_clean_reboot(self):
        self.prepare()
        key = (self.receipt/'duck-home.key').read_bytes()
        self.assertRegex(key, rb'^[a-f0-9]{64}\n\Z')
        self.assertEqual(stat.S_IMODE((self.receipt/'duck-home.key').stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.receipt.stat().st_mode), 0o700)
        with self.assertRaises(ValueError):
            self.prepare()  # Card retains the key the owner already received.
        self.boot()
        self.assertEqual((self.dest/'calibration.toml').read_bytes(), self.cal.read_bytes())
        self.assertEqual((self.dest/'home.key').read_bytes(), key)
        self.assertEqual(stat.S_IMODE((self.dest/'home.key').stat().st_mode), 0o640)
        self.assertEqual((self.dest/'home.key').stat().st_gid, os.getgid())
        fingerprint = hashlib.sha256(bytes.fromhex(key.decode())).hexdigest()[:16]
        self.assertEqual((self.config/'DUCKOWN.ID').read_text(), fingerprint+'\n')
        self.assertEqual({p.name for p in self.config.iterdir()}, {'DUCKBASE.ID','DUCKMOT.ID','DUCKOWN.ID'})
        self.assertEqual((self.tmp/'etc/ssh/duck-keys/radxa').read_text(), 'ssh-ed25519 ZmFrZQ==\n')
        profile = self.tmp/'etc/NetworkManager/system-connections/duck-home.nmconnection'
        self.assertIn('psk='+hashlib.pbkdf2_hmac('sha1',b'test-passphrase','home=家'.encode(),4096,32).hex(), profile.read_text())
        self.assertNotIn('test-passphrase', profile.read_text())
        self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o600)
        self.assertEqual([c.args[0][0] for c in self.run_os.call_args_list], ['systemctl','nmcli','nmcli','chpasswd'])
        self.run_os.reset_mock()
        self.boot()
        self.run_os.assert_not_called()
        self.assertEqual((self.dest/'home.key').read_bytes(), key)
        with self.assertRaises(ValueError):
            self.prepare()

    def test_public_observation_image_with_legacy_calibration(self):
        (self.config/'DUCKMOT.ID').unlink()
        self.marker.unlink()
        self.prepare()
        self.assertFalse((self.receipt/'duck-home.key').exists())
        self.boot()
        self.assertEqual((self.dest/'calibration.toml').read_bytes(), self.cal.read_bytes())
        self.assertFalse((self.dest/'home.key').exists())
        self.assertFalse((self.config/'duck-setup.json').exists())
        self.assertFalse((self.config/'duck-calibration.toml').exists())

    def test_motion_image_without_calibration_stays_observation_only(self):
        self.prepare(with_cal=False)
        self.boot()
        self.assertFalse((self.dest/'home.key').exists())
        self.assertFalse((self.receipt/'duck-home.key').exists())

    def test_wrong_joint_mapping_rejected_before_receipt_or_card_write(self):
        self.cal.write_bytes(self.cal.read_bytes().replace(b'head_roll',b'mouth'))
        with self.assertRaisesRegex(ValueError, '名称'):
            self.prepare()
        self.assertFalse(self.receipt.exists())
        self.assertFalse((self.config/'duck-setup.json').exists())

    def test_first_boot_revalidates_digest_and_calibration_before_writing(self):
        self.prepare()
        path = self.config/'duck-setup.json'
        setup = json.loads(path.read_bytes())
        raw = b'motion_enabled = false'
        setup['motion']['calibration_b64'] = base64.b64encode(raw).decode()
        path.write_text(json.dumps(setup))
        with self.assertRaisesRegex(ValueError, '摘要'):
            self.boot()
        setup['motion']['calibration_sha256'] = hashlib.sha256(raw).hexdigest()
        path.write_text(json.dumps(setup))
        with self.assertRaisesRegex(ValueError, '标定'):
            self.boot()
        self.run_os.assert_not_called()
        self.assertFalse(self.dest.exists())
        self.assertTrue(path.is_file())

    def test_missing_runtime_and_preexisting_different_owner_or_calibration_fail_closed(self):
        self.prepare()
        self.marker.unlink()
        with self.assertRaises(FileNotFoundError):
            self.boot()
        self.marker.write_bytes(calibration.MOTION_MARKER)
        self.dest.mkdir(parents=True)
        (self.dest/'home.key').write_bytes(b'0'*64+b'\n')
        with self.assertRaisesRegex(ValueError, 'home.key'):
            self.boot()
        self.assertEqual((self.dest/'home.key').read_bytes(), b'0'*64+b'\n')
        (self.dest/'home.key').unlink()
        (self.dest/'calibration.toml').write_bytes(b'previous calibration')
        with self.assertRaisesRegex(ValueError, 'calibration.toml'):
            self.boot()
        self.assertEqual((self.dest/'calibration.toml').read_bytes(), b'previous calibration')
        self.run_os.assert_not_called()

    def test_interruption_between_calibration_and_key_can_be_retried_with_receipt(self):
        self.prepare()
        store = provision.store
        def interrupt_key(path, *args, **kwargs):
            if path.name == 'home.key':
                raise OSError('simulated interruption')
            store(path, *args, **kwargs)
        with patch.object(provision, 'store', side_effect=interrupt_key):
            with self.assertRaisesRegex(OSError, 'interruption'):
                self.boot()
        self.assertFalse((self.dest/'home.key').exists())
        self.assertTrue((self.config/'duck-setup.json').is_file())
        self.boot()
        self.assertEqual((self.receipt/'duck-home.key').read_bytes(), (self.dest/'home.key').read_bytes())

    def test_private_receipt_cannot_be_on_the_card(self):
        self.receipt=self.config/'private'
        with self.assertRaisesRegex(ValueError, '私人'):
            self.prepare()
        self.assertFalse(self.receipt.exists())
