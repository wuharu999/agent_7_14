import sqlite3
from ecs.app.config import DATABASE_PATH

def migrate():
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute("PRAGMA table_info(users)")
        columns = [row["name"] for row in cursor.fetchall()]
        if "email" not in columns:
            # We add email as nullable first
            connection.execute("ALTER TABLE users ADD COLUMN email TEXT")
            # Populate with a default to satisfy UNIQUE if we make it unique. 
            # SQLite ALTER TABLE cannot ADD COLUMN with UNIQUE constraint directly if it's not NULL and has no default that is unique.
            connection.execute("UPDATE users SET email = username || '@localhost' WHERE email IS NULL")
            # We can't easily enforce UNIQUE on an existing table without recreating it in SQLite, 
            # but we can create a UNIQUE INDEX.
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email)")
            
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS qa_visitors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip_address TEXT NOT NULL,
                user_id INTEGER,
                visited_at TEXT NOT NULL
            );
            
            CREATE TABLE IF NOT EXISTS wiki_metrics (
                date TEXT PRIMARY KEY,
                new_entries INTEGER NOT NULL DEFAULT 0
            );
            
            CREATE TABLE IF NOT EXISTS team_settings (
                team_name TEXT PRIMARY KEY,
                auto_review_enabled INTEGER NOT NULL DEFAULT 0,
                last_review_at TEXT,
                prev_review_at TEXT
            );
            
            CREATE TABLE IF NOT EXISTS team_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('member', 'captain')),
                joined_at TEXT NOT NULL,
                UNIQUE(team_name, user_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            
            CREATE TABLE IF NOT EXISTS team_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_name TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('pending', 'approved', 'denied')),
                requested_at TEXT NOT NULL,
                UNIQUE(team_name, user_id),
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            );
            
            CREATE INDEX IF NOT EXISTS idx_qa_visitors_date ON qa_visitors(visited_at);
        """)

if __name__ == "__main__":
    migrate()
