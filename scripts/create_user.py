#!/usr/bin/env python3
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ecs.app.auth import create_or_update_user  # noqa: E402
from ecs.app.database import initialize_database  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or update an Agent1 web user")
    parser.add_argument("--username", required=True)
    parser.add_argument("--role", choices=("viewer", "editor", "admin"), default="editor")
    parser.add_argument("--teams", default="", help="Comma-separated list of teams this user can manage")
    args = parser.parse_args()

    password = getpass.getpass("Password (minimum 10 characters): ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")

    initialize_database()
    user_id = create_or_update_user(args.username, password, args.role, args.teams)
    print(f"User ready: id={user_id} username={args.username.lower()} role={args.role} teams={args.teams}")


if __name__ == "__main__":
    main()
