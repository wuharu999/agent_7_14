import pytest
from ecs.app.database import (
    _connect,
    _DB_LOCK,
    initialize_database,
    reconcile_existing_uploads,
    utc_now,
)

def test_reconcile_uploads():
    initialize_database()
    
    upload_id = "test_sync_uid"
    team = "walker_s2"
    
    with _DB_LOCK, _connect() as connection:
        connection.execute("DELETE FROM uploads WHERE upload_id = ?", (upload_id,))
        connection.execute("DELETE FROM upload_sources WHERE upload_id = ?", (upload_id,))
        
        now = utc_now()
        connection.execute(
            """
            INSERT INTO uploads (upload_id, task_id, team, filename, size_bytes, ecs_path, status, stage, message, created_at, updated_at)
            VALUES (?, ?, ?, 'test.zip', 100, '/tmp', 'completed', 'ingestion_completed', '', ?, ?)
            """,
            (upload_id, "task_1", team, now, now),
        )
        
        connection.execute(
            """
            INSERT INTO upload_sources (upload_id, source_identity, status, updated_at)
            VALUES (?, ?, 'completed', ?)
            """,
            (upload_id, "walker_s2/test_sync_uid/file1.md", now),
        )
        
    # Reconcile when missing on disk (disk is empty list)
    reconcile_existing_uploads([])
    
    with _DB_LOCK, _connect() as connection:
        upload = connection.execute("SELECT status FROM uploads WHERE upload_id = ?", (upload_id,)).fetchone()
        source = connection.execute("SELECT status FROM upload_sources WHERE upload_id = ?", (upload_id,)).fetchone()
        
        assert upload["status"] == "sources_removed"
        assert source["status"] == "deleted"
        
    # Reconcile when restored on disk
    reconcile_existing_uploads([{"team": team, "upload_id": upload_id}])
    
    with _DB_LOCK, _connect() as connection:
        upload = connection.execute("SELECT status FROM uploads WHERE upload_id = ?", (upload_id,)).fetchone()
        source = connection.execute("SELECT status FROM upload_sources WHERE upload_id = ?", (upload_id,)).fetchone()
        
        assert upload["status"] == "completed"
        assert source["status"] == "completed"
