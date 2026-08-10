#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

def main():
    if len(sys.argv) != 2:
        print("Usage: python add_team.py <team_name>")
        sys.exit(1)

    team_name = sys.argv[1].strip()
    if not team_name:
        print("Team name cannot be empty")
        sys.exit(1)

    # Resolve project root (assuming script is in scripts/)
    project_root = Path(__file__).resolve().parent.parent

    # Read existing teams from worker/teams.json
    teams_json_path = project_root / "worker" / "teams.json"
    if not teams_json_path.exists():
        print(f"Error: {teams_json_path} does not exist.")
        sys.exit(1)

    with open(teams_json_path, "r", encoding="utf-8") as f:
        teams_data = json.load(f)

    if team_name in teams_data["teams"]:
        print(f"Team '{team_name}' already exists in teams.json.")
        sys.exit(0)

    # Determine paths
    agent_base = project_root / "agent1"
    base_dir = agent_base / team_name
    staging_dir = base_dir / ".agent1-worker" / "staging"
    trash_dir = base_dir / ".agent1-trash"
    llm_wiki_queue_file = base_dir / ".llm-wiki" / "ingest-queue.json"
    llm_wiki_cache_file = base_dir / ".llm-wiki" / "ingest-cache.json"

    # Assume a dynamic port allocation rule for llm_wiki_api_url based on existing entries
    # Find max port used currently
    max_port = 19820
    for team, config in teams_data["teams"].items():
        url = config.get("llm_wiki_api_url", "")
        if url.startswith("http://127.0.0.1:"):
            try:
                port = int(url.split(":")[-1].split("/")[0])
                if port > max_port:
                    max_port = port
            except ValueError:
                pass

    new_port = max_port + 1

    # Create the base config for the new team
    new_team_config = {
        "base_dir": str(base_dir.relative_to(project_root)),
        "staging_dir": str(staging_dir.relative_to(project_root)),
        "trash_dir": str(trash_dir.relative_to(project_root)),
        "llm_wiki_queue_file": str(llm_wiki_queue_file.relative_to(project_root)),
        "llm_wiki_cache_file": str(llm_wiki_cache_file.relative_to(project_root)),
        "llm_wiki_api_url": f"http://127.0.0.1:{new_port}/api/v1"
    }

    # Add to teams.json
    teams_data["teams"][team_name] = new_team_config
    with open(teams_json_path, "w", encoding="utf-8") as f:
        json.dump(teams_data, f, indent=4, ensure_ascii=False)
        f.write("\n")

    # Now create the actual folder structure if it doesn't exist
    print(f"Creating folders for team: {team_name}")
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "raw" / "sources").mkdir(parents=True, exist_ok=True)
    (base_dir / "wiki").mkdir(parents=True, exist_ok=True)
    (base_dir / ".llm-wiki").mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    trash_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Successfully added team '{team_name}'.")
    print(f"Update ecs/app/config.py ALLOWED_TEAMS to include '{team_name}'.")
    print(f"Make sure to start an LLM Wiki instance for this team on port {new_port}.")

if __name__ == "__main__":
    main()
