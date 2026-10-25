import unittest
import tempfile
import threading
import json
from pathlib import Path
from unittest.mock import patch

# Add backend directory
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from persistence import SQLitePersistence

class TestPersistenceSQLite(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_nexus.db"
        self.store = SQLitePersistence(self.db_file)

    def test_seeding_and_initial_state(self):
        projects = self.store.get_projects()
        self.assertGreaterEqual(len(projects), 3)

        workflows = self.store.get_workflows()
        self.assertGreaterEqual(len(workflows), 2)

        settings = self.store.get_settings()
        self.assertIn("dark", settings)
        self.assertIn("model", settings)


    def test_custom_database_does_not_import_local_user_json(self):
        # Isolated test DBs must not silently copy the application's real local
        # state file; migration is opt-in for custom database paths.
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy-user-state.json"
            db_file = Path(tmp) / "isolated.db"
            legacy.write_text(json.dumps({
                "projects": [["Private Local Project", "local", "not for tests", "Live"]],
                "workflows": [],
                "settings": {"model": "Gemini 2.5 Pro"},
            }), encoding="utf-8")
            with patch("persistence.LEGACY_JSON_FILE", legacy):
                isolated = SQLitePersistence(db_file)
            names = [p[0] for p in isolated.get_projects()]
            self.assertNotIn("Private Local Project", names)

    def test_explicit_legacy_json_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            legacy = Path(tmp) / "legacy-user-state.json"
            db_file = Path(tmp) / "migrated.db"
            legacy.write_text(json.dumps({
                "projects": [["Migrated Project", "Python", "Migrated description", "Current"]],
                "workflows": [["Migrated Workflow", "R → V", "2 nodes"]],
                "settings": {"dark": False, "model": "Gemini 2.5 Flash", "temp": 20, "fed": False},
            }), encoding="utf-8")
            migrated = SQLitePersistence(db_file, legacy_json_path=legacy)
            self.assertIn("Migrated Project", [p[0] for p in migrated.get_projects()])
            self.assertIn("Migrated Workflow", [w[0] for w in migrated.get_workflows()])
            self.assertEqual(migrated.get_settings()["model"], "Gemini 2.5 Flash")

    def test_concurrent_writes_zero_lost_updates(self):
        # Initial project count
        init_count = len(self.store.get_projects())
        num_threads = 30
        threads = []
        errors = []

        def worker(idx):
            try:
                self.store.add_project(f"Concurrent-Project-{idx}")
            except Exception as e:
                errors.append(e)

        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Zero errors
        self.assertEqual(len(errors), 0, f"Encountered concurrency errors: {errors}")

        # Exact count check: all 30 updates must be present!
        final_projects = self.store.get_projects()
        self.assertEqual(len(final_projects), init_count + num_threads)

        # Verify all names exist
        names = {p[0] for p in final_projects}
        for i in range(num_threads):
            self.assertIn(f"Concurrent-Project-{i}", names)

    def test_persistence_across_reconnect(self):
        # Add project and workflow
        self.store.add_project("Persistent Lab")
        self.store.add_workflow("Persistent Pipeline")
        self.store.update_settings({"temp": 99})

        # Reopen database instance
        new_store = SQLitePersistence(self.db_file)
        self.assertEqual(new_store.get_projects()[0][0], "Persistent Lab")
        self.assertEqual(new_store.get_workflows()[-1][0], "Persistent Pipeline")
        self.assertEqual(new_store.get_settings()["temp"], 99)

    def test_delete_lifecycle(self):
        init_len = len(self.store.get_projects())
        self.store.add_project("To Delete")
        self.assertEqual(len(self.store.get_projects()), init_len + 1)
        self.store.delete_project_by_index(0)
        self.assertEqual(len(self.store.get_projects()), init_len)

if __name__ == "__main__":
    unittest.main()
