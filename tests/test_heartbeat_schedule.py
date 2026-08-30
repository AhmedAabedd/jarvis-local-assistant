from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mounir import db


class HeartbeatScheduleTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_db_path = db.DB_PATH
        self.old_legacy_path = db.LEGACY_REGISTRY
        db.DB_PATH = Path(self.temp_dir.name) / "mounir.db"
        db.LEGACY_REGISTRY = Path(self.temp_dir.name) / "legacy.json"
        db.init()

    def tearDown(self):
        db.DB_PATH = self.old_db_path
        db.LEGACY_REGISTRY = self.old_legacy_path
        self.temp_dir.cleanup()

    def test_monthly_intervals_are_supported(self):
        task = db.create_heartbeat_task(
            name="Monthly review",
            instructions="Review the month.",
            interval_minutes=30 * 24 * 60,
        )

        self.assertEqual(task["interval_minutes"], 43200)

    def test_intervals_remain_bounded_to_one_year(self):
        with self.assertRaisesRegex(ValueError, "between 5 minutes and 1 year"):
            db.create_heartbeat_task(
                name="Too distant",
                instructions="This should be rejected.",
                interval_minutes=(365 * 24 * 60) + 1,
            )


if __name__ == "__main__":
    unittest.main()
