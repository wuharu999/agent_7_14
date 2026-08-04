import os
import zipfile
from pathlib import Path

def pack_release():
    project_root = Path(__file__).parent.parent.resolve()
    zip_path = project_root / "release.zip"
    
    exclude_dirs = {
        ".venv-ecs",
        ".venv-worker",
        "__pycache__",
        "ecs-data",
        ".git",
        ".pytest_cache",
        ".github",
        ".agents",
        ".codex",
        ".vscode",
    }
    
    exclude_files = {
        "ecs/.env",
        "worker/.env",
        "release.zip",
    }
    
    exclude_prefixes = {
        "agent1/agent/raw/",
        "agent1/agent/wiki/",
        "agent1/agent/.llm-wiki/",
        "agent1/agent/.agent1-trash/",
        "agent1/agent/.agent1-worker/",
    }
    
    print(f"Creating release archive at {zip_path}...")
    
    count = 0
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        for root, dirs, files in os.walk(project_root):
            # Skip excluded directory paths in os.walk search
            rel_root_path = Path(root).relative_to(project_root)
            parts = rel_root_path.parts
            
            if any(p in exclude_dirs for p in parts):
                continue
                
            for file in files:
                file_path = rel_root_path / file
                file_str = file_path.as_posix()
                
                # Apply exclusions
                if file_str in exclude_files or file == '.DS_Store' or file.endswith('.pyc') or file.endswith('.pyo'):
                    continue
                if any(file_str.startswith(pref) for pref in exclude_prefixes):
                    continue
                    
                full_path = project_root / file_path
                zipf.write(full_path, file_str)
                count += 1
                
    print(f"Packaged {count} files successfully into release.zip.")

if __name__ == "__main__":
    pack_release()
