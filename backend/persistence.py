import sqlite3
import json
import os
import threading
from pathlib import Path
from typing import Any, Optional

DEFAULT_DB_FILE = Path(os.getenv("NEXUS_DB_PATH", str(Path(__file__).parent / "nexus_state.db"))).expanduser()
LEGACY_JSON_FILE = Path(__file__).parent / "nexus_db.json"
EXAMPLE_JSON_FILE = Path(__file__).parent / "nexus_db.example.json"

DEFAULT_STATE = {
    "projects": [
        ["Agri-Neural Twin", "React · Gemini · Agentic AI", "Climate-resilient farming intelligence", "Portfolio"],
        ["OptiFlow Digital Twin", "React · Three.js · Node.js", "Factory digital twin + AGV orchestration", "Portfolio"],
        ["NEXUS RAG OS", "LangGraph · MCP · Pinecone · Qdrant", "Grounded multi-agent knowledge OS", "Current"]
    ],
    "workflows": [
        ["Research Brief", "Retrieve → compare → verify → synthesize", "4 nodes"],
        ["Code Review", "Index repo → inspect → detect risk → explain", "5 nodes"]
    ],
    "settings": {
        "dark": True,
        "model": "Gemini 3.5 Flash Lite",
        "temp": 15,
        "fed": True
    }
}

class SQLitePersistence:
    def __init__(self, db_path: Optional[Path] = None, legacy_json_path: Optional[Path] = None):
        selected_path = Path(db_path or DEFAULT_DB_FILE).expanduser()
        selected_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = str(selected_path)
        # By default, only the application's default database may migrate its
        # colocated legacy JSON file. Tests and custom databases must not read
        # local user state unless they explicitly opt in with legacy_json_path.
        self.legacy_json_path = (
            Path(legacy_json_path).expanduser()
            if legacy_json_path is not None
            else (LEGACY_JSON_FILE if db_path is None else None)
        )
        self._lock = threading.Lock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._lock, self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    stack TEXT NOT NULL,
                    description TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    nodes TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
            """)
            conn.commit()

            # Check if seeding or migration is needed
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) as cnt FROM projects")
            if cur.fetchone()["cnt"] == 0:
                self._migrate_or_seed(conn)

    def _migrate_or_seed(self, conn: sqlite3.Connection):
        data = None
        # Try loading from existing legacy json
        if self.legacy_json_path and self.legacy_json_path.exists():
            try:
                with open(self.legacy_json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        if not data and EXAMPLE_JSON_FILE.exists():
            try:
                with open(EXAMPLE_JSON_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                pass

        if not data:
            data = DEFAULT_STATE

        # Seed projects
        for p in data.get("projects", []):
            name = p[0] if len(p) > 0 else "Project"
            stack = p[1] if len(p) > 1 else "React · Node.js"
            desc = p[2] if len(p) > 2 else "Custom AI Project"
            status = p[3] if len(p) > 3 else "Live"
            conn.execute(
                "INSERT INTO projects (name, stack, description, status) VALUES (?, ?, ?, ?)",
                (name, stack, desc, status)
            )

        # Seed workflows
        for w in data.get("workflows", []):
            name = w[0] if len(w) > 0 else "Workflow"
            desc = w[1] if len(w) > 1 else "Automation chain"
            nodes = w[2] if len(w) > 2 else "3 nodes"
            conn.execute(
                "INSERT INTO workflows (name, description, nodes) VALUES (?, ?, ?)",
                (name, desc, nodes)
            )

        # Seed settings
        settings = data.get("settings", DEFAULT_STATE["settings"])
        for k, v in settings.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))

        conn.commit()

    # Projects CRUD
    def get_projects(self) -> list[list[str]]:
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, stack, description, status FROM projects ORDER BY id DESC")
            rows = cur.fetchall()
            return [[r["name"], r["stack"], r["description"], r["status"]] for r in rows]

    def add_project(self, name: str, stack: str = "React · Node.js", desc: str = "Custom deployed AI project", status: str = "Current") -> list[list[str]]:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "INSERT INTO projects (name, stack, description, status) VALUES (?, ?, ?, ?)",
                (name, stack, desc, status)
            )
            conn.commit()
        return self.get_projects()

    def delete_project_by_index(self, index: int) -> list[list[str]]:
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM projects ORDER BY id DESC")
            ids = [r["id"] for r in cur.fetchall()]
            if 0 <= index < len(ids):
                target_id = ids[index]
                conn.execute("DELETE FROM projects WHERE id = ?", (target_id,))
                conn.commit()
        return self.get_projects()

    # Workflows CRUD
    def get_workflows(self) -> list[list[str]]:
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id, name, description, nodes FROM workflows ORDER BY id ASC")
            rows = cur.fetchall()
            return [[r["name"], r["description"], r["nodes"]] for r in rows]

    def add_workflow(self, name: str, desc: str = "Custom workflow chain", nodes: str = "3 nodes") -> list[list[str]]:
        with self._lock, self._get_connection() as conn:
            conn.execute(
                "INSERT INTO workflows (name, description, nodes) VALUES (?, ?, ?)",
                (name, desc, nodes)
            )
            conn.commit()
        return self.get_workflows()

    def delete_workflow_by_index(self, index: int) -> list[list[str]]:
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT id FROM workflows ORDER BY id ASC")
            ids = [r["id"] for r in cur.fetchall()]
            if 0 <= index < len(ids):
                target_id = ids[index]
                conn.execute("DELETE FROM workflows WHERE id = ?", (target_id,))
                conn.commit()
        return self.get_workflows()

    # Settings CRUD
    def get_settings(self) -> dict[str, Any]:
        with self._lock, self._get_connection() as conn:
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings")
            rows = cur.fetchall()
            res = {}
            for r in rows:
                try:
                    res[r["key"]] = json.loads(r["value"])
                except Exception:
                    res[r["key"]] = r["value"]
            return res if res else DEFAULT_STATE["settings"]

    def update_settings(self, updates: dict[str, Any]) -> dict[str, Any]:
        with self._lock, self._get_connection() as conn:
            for k, v in updates.items():
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, json.dumps(v)))
            conn.commit()
        return self.get_settings()

PERSISTENCE = SQLitePersistence()
