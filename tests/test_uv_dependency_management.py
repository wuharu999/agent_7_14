from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_uv_metadata_and_lock_cover_both_runtimes() -> None:
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert '[project.optional-dependencies]' in project
    assert 'ecs = [' in project
    assert 'worker = [' in project
    assert '"openai>=1.0,<3"' in project
    assert 'cloud-sdk' not in project
    assert (ROOT / "uv.lock").is_file()


def test_runtime_dependency_scripts_use_locked_uv_sync() -> None:
    sync_script = (ROOT / "scripts" / "uv_sync.sh").read_text(encoding="utf-8")
    assert "uv_bin\" sync" in sync_script
    assert "--locked" in sync_script
    assert "--no-managed-python" in sync_script

    for relative in (
        "scripts/bootstrap_ecs.sh",
        "scripts/bootstrap_worker.sh",
        "scripts/pull_and_restart_ecs.sh",
        "scripts/pull_and_restart_worker.sh",
        "scripts/deploy_worker.sh",
        "scripts/deploy_worker_from_downloads.sh",
    ):
        script = (ROOT / relative).read_text(encoding="utf-8")
        assert "pip install" not in script


def test_legacy_requirements_files_are_removed() -> None:
    assert not (ROOT / "ecs" / "requirements.txt").exists()
    assert not (ROOT / "worker" / "requirements.txt").exists()
