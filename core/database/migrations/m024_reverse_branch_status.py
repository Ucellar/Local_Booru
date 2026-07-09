from __future__ import annotations

NAME = "reverse_branch_status"


def apply(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS reverse_branch_status (
            original_path TEXT NOT NULL,
            branch_key TEXT NOT NULL,
            scan_revision INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(original_path, branch_key, scan_revision)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_reverse_branch_status_path ON reverse_branch_status(original_path, scan_revision, status)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_reverse_branch_status_branch ON reverse_branch_status(branch_key, scan_revision, status)")
