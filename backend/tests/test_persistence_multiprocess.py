import unittest
import tempfile
import multiprocessing as mp
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from persistence import SQLitePersistence

def _mp_worker(db_path_str: str, worker_id: int, count: int, error_queue: mp.Queue):
    """
    Independent OS process writing to the shared SQLite WAL database.
    """
    try:
        store = SQLitePersistence(Path(db_path_str))
        for i in range(count):
            store.add_project(
                name=f"MP-Worker-{worker_id}-Project-{i}",
                stack="Python · Multiprocessing",
                desc=f"Process concurrency test item {i}",
                status="Active"
            )
            # Occasional read to test concurrent read-during-write
            if i % 3 == 0:
                _ = store.get_projects()
    except Exception as e:
        error_queue.put((worker_id, str(e)))

class TestPersistenceMultiProcess(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_file = Path(self.temp_dir) / "test_multiprocess.db"
        # Seed the initial store
        self.store = SQLitePersistence(self.db_file)

    def test_multiprocess_concurrent_writes(self):
        """
        Verify multi-process concurrency on Windows with WAL mode and 10s busy-timeout.
        5 separate OS processes write 8 records each (40 total) concurrently.
        """
        init_count = len(self.store.get_projects())
        num_processes = 5
        writes_per_proc = 8
        expected_total = init_count + (num_processes * writes_per_proc)

        ctx = mp.get_context("spawn")
        error_queue = ctx.Queue()
        processes = []

        for p_id in range(num_processes):
            p = ctx.Process(
                target=_mp_worker,
                args=(str(self.db_file), p_id, writes_per_proc, error_queue)
            )
            processes.append(p)

        for p in processes:
            p.start()

        for p in processes:
            p.join(timeout=30)

        # Collect any errors reported by subprocesses
        errors = []
        while not error_queue.empty():
            errors.append(error_queue.get())

        self.assertEqual(len(errors), 0, f"Subprocesses encountered concurrency errors: {errors}")

        # Check total count and verify zero lost updates
        final_projects = self.store.get_projects()
        self.assertEqual(
            len(final_projects),
            expected_total,
            f"Lost updates detected! Expected {expected_total}, got {len(final_projects)}"
        )

        # Verify every worker's projects were persisted
        names = {p[0] for p in final_projects}
        for p_id in range(num_processes):
            for i in range(writes_per_proc):
                expected_name = f"MP-Worker-{p_id}-Project-{i}"
                self.assertIn(expected_name, names)

    def test_idempotent_reinit_preserves_records(self):
        """
        Opening an existing database instance should not overwrite or re-seed data.
        """
        self.store.add_project("Custom Pre-existing Project")
        count_before = len(self.store.get_projects())

        # Re-initialize multiple times
        store2 = SQLitePersistence(self.db_file)
        store3 = SQLitePersistence(self.db_file)

        self.assertEqual(len(store2.get_projects()), count_before)
        self.assertEqual(len(store3.get_projects()), count_before)
        self.assertIn("Custom Pre-existing Project", [p[0] for p in store3.get_projects()])

    def test_transaction_rollback_on_failure(self):
        """
        Verify transaction integrity: if an error occurs within a transaction,
        partial writes must be rolled back.
        """
        count_before = len(self.store.get_projects())
        try:
            with self.store._get_connection() as conn:
                conn.execute("INSERT INTO projects (name, stack, description, status) VALUES (?, ?, ?, ?)",
                             ("Temporary Valid Record", "Stack", "Desc", "Live"))
                # Trigger constraint or syntax error
                conn.execute("INSERT INTO nonexistent_table VALUES (1, 2, 3)")
                conn.commit()
        except Exception:
            pass  # Expected error

        # The first insert should have been rolled back
        count_after = len(self.store.get_projects())
        self.assertEqual(count_after, count_before, "Failed transaction was not cleanly rolled back!")

if __name__ == "__main__":
    unittest.main()
