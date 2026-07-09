import sqlite3

from core.database.schema import init_db


def test_clean_database_reaches_schema_v24_and_records_m024():
    con = sqlite3.connect(':memory:')
    con.row_factory = sqlite3.Row
    init_db(con, force=True)
    assert con.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] == '24'
    row = con.execute("SELECT name,status FROM schema_migrations WHERE version=24").fetchone()
    assert dict(row) == {'name': 'reverse_branch_status', 'status': 'applied'}
    assert con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reverse_branch_status'").fetchone()
