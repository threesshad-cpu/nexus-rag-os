import unittest
import os
import json
import tempfile
import threading
from pathlib import Path

# Add backend directory
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

class TestConcurrencyRace(unittest.TestCase):
    def test_reproduce_json_lost_updates(self):
        # This test intentionally demonstrates why atomic replace alone does
        # not make a read-modify-write transaction safe under concurrency.
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = os.path.join(temp_dir, "test_race.json")
            with open(db_path, "w", encoding="utf-8") as f:
                json.dump({"projects": []}, f)

            num_updates = 20
            all_read = threading.Barrier(num_updates)
            errors = []
            errors_lock = threading.Lock()

            def flawed_add_project(name):
                try:
                    with open(db_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    # Force every thread to read the same old snapshot before
                    # any thread writes, making lost updates deterministic.
                    all_read.wait(timeout=5)
                    data["projects"].append([name, "desc", "stack", "Live"])
                    tmp = f"{db_path}.{threading.get_ident()}.tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(data, f)
                    os.replace(tmp, db_path)
                except Exception as exc:
                    with errors_lock:
                        errors.append(repr(exc))

            threads = [
                threading.Thread(target=flawed_add_project, args=(f"Project-{i}",))
                for i in range(num_updates)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=10)

            self.assertFalse(any(thread.is_alive() for thread in threads), "Race reproduction hung")
            self.assertEqual(errors, [], f"Unexpected errors in controlled reproduction: {errors}")
            with open(db_path, "r", encoding="utf-8") as f:
                final_data = json.load(f)

            lost_updates = num_updates - len(final_data["projects"])
            print(f"\n[RACE DEMO] Expected {num_updates} projects, found {len(final_data['projects'])}, Lost: {lost_updates}")
            self.assertGreater(lost_updates, 0, "Expected lost updates from unprotected read-modify-write")

if __name__ == "__main__":
    unittest.main()
