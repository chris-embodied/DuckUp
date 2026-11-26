from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import migrate
import read_active_calibration as reader


class MigrationAssistantTests(unittest.TestCase):
    def test_uses_the_running_driver_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for service, calibration in reader.CALIBRATIONS.items():
                path = root/calibration.lstrip("/")
                path.parent.mkdir(parents=True, exist_ok=True)
                profile = path.with_name("loaded.toml")
                profile.write_text(f'[bus]\nfeetech_calibration = "{calibration}"')
                for args in (["--params", "/"+str(profile.relative_to(root))],
                             ["--params=/"+str(profile.relative_to(root))]):
                    self.assertEqual(reader.selected_file(["/opt/robotd", *args], service, root), path)
                profile.write_text('[bus]\nfeetech_calibration = "/package/example.toml"')
                with self.assertRaises(ValueError):
                    reader.selected_file(["robotd", *args], service, root)

    def test_ambiguous_driver_does_not_choose_a_file(self):
        found = subprocess.CompletedProcess([], 0, stdout="123\n")
        with patch.object(reader.subprocess, "run", return_value=found):
            with self.assertRaisesRegex(ValueError, "无法确定"):
                reader.read_active()

    def test_only_one_marked_motion_volume_can_be_selected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, new = root/"old", root/"new"
            old.mkdir()
            new.mkdir()
            (new/"DUCKBASE.ID").write_bytes(b"duck-base-zero3w-v1\n")
            for vol in (old, new):
                (vol/"DUCKMOT.ID").write_bytes(migrate.MOTION_MARKER)
            self.assertEqual(migrate.motion_volume([old, new]), new)
            (old/"DUCKBASE.ID").write_bytes((new/"DUCKBASE.ID").read_bytes())
            with self.assertRaisesRegex(ValueError, "多张"):
                migrate.motion_volume([old, new])
            (new/"DUCKMOT.ID").unlink()
            with self.assertRaisesRegex(ValueError, "没有找到"):
                migrate.motion_volume([new])

    def test_export_fails_before_backup_on_bad_host_or_bad_data(self):
        with tempfile.TemporaryDirectory() as temp:
            backup_root = Path(temp)/"private"
            with patch.object(migrate.subprocess, "run") as run:
                with self.assertRaises(ValueError):
                    migrate.export_calibration("-oProxyCommand=wrong", backup_root)
                run.assert_not_called()
                run.return_value = subprocess.CompletedProcess([], 0, stdout=b"motion_enabled = false")
                with self.assertRaisesRegex(ValueError, "标定未通过"):
                    migrate.export_calibration("duckbase.local", backup_root)
            self.assertFalse(backup_root.exists())
