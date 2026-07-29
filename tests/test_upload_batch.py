from __future__ import annotations

import asyncio
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from ecs.app import auth, database
from ecs.app.config import SESSION_COOKIE_NAME
from ecs.app.routes import uploads
from shared.source_types import (
    ARCHIVE_UPLOAD_SUFFIXES,
    DOCUMENT_SOURCE_SUFFIXES,
    SUPPORTED_SOURCE_SUFFIXES,
    SUPPORTED_UPLOAD_SUFFIXES,
    TEXT_SOURCE_SUFFIXES,
    is_supported_upload,
)

ROOT = Path(__file__).resolve().parents[1]
UPLOAD_TEMPLATE = ROOT / "ecs" / "app" / "templates" / "upload.html"
UPLOAD_STATUS_TEMPLATE = ROOT / "ecs" / "app" / "templates" / "upload_status.html"


def _request(session_token: str = "") -> Request:
    headers = []
    if session_token:
        headers.append(
            (b"cookie", f"{SESSION_COOKIE_NAME}={session_token}".encode("ascii"))
        )
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/upload",
            "raw_path": b"/upload",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "root_path": "",
        }
    )


class _MemoryUpload:
    def __init__(self, filename: str, content: bytes) -> None:
        self.filename = filename
        self.file = io.BytesIO(content)

    async def close(self) -> None:
        self.file.close()


async def _run_inline(function, *args):
    return function(*args)


class SharedSourceTypeTests(unittest.TestCase):
    def test_source_and_outer_upload_formats_are_consistent(self) -> None:
        self.assertEqual(
            SUPPORTED_SOURCE_SUFFIXES,
            frozenset(DOCUMENT_SOURCE_SUFFIXES + TEXT_SOURCE_SUFFIXES),
        )
        self.assertEqual(ARCHIVE_UPLOAD_SUFFIXES, (".zip",))
        self.assertEqual(
            SUPPORTED_UPLOAD_SUFFIXES,
            frozenset((*SUPPORTED_SOURCE_SUFFIXES, ".zip")),
        )

    def test_upload_suffix_validation_is_case_insensitive(self) -> None:
        for filename in (
            "guide.PDF",
            "机器人说明.DOCX",
            "slides.pptx",
            "table.XLSX",
            "readme.MDX",
            "配置.YAML",
            "sources.ZIP",
        ):
            self.assertTrue(is_supported_upload(filename), filename)
        for filename in ("script.exe", "archive.tar.gz", "no-extension"):
            self.assertFalse(is_supported_upload(filename), filename)


class UploadEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name)
        self.database_patch = patch.object(
            database, "DATABASE_PATH", self.root / "agent_jobs.db"
        )
        self.upload_root_patch = patch.object(
            uploads, "UPLOAD_ROOT", self.root / "uploads"
        )
        self.teams_patch = patch.object(
            uploads, "get_allowed_teams", return_value=["tian_gong", "walker_s2", "walker_c1"]
        )
        # Python 3.14's executor wake-up pipe is unavailable in this sandbox.
        # Keep this route unit test synchronous at the filesystem boundary.
        self.to_thread_patch = patch.object(uploads.asyncio, "to_thread", _run_inline)
        self.database_patch.start()
        self.upload_root_patch.start()
        self.teams_patch.start()
        self.to_thread_patch.start()
        database.initialize_database()
        self.editor_token, self.editor_csrf = self._create_session("editor", "editor", "tian_gong,walker_s2,walker_c1")

    def tearDown(self) -> None:
        self.to_thread_patch.stop()
        self.teams_patch.stop()
        self.upload_root_patch.stop()
        self.database_patch.stop()
        self.temporary_directory.cleanup()

    @staticmethod
    def _create_session(username: str, role: str, teams: str = "") -> tuple[str, str]:
        user_id = database.create_user_record(
            username=username,
            email=f"{username}@example.com",
            password_hash="unused-in-route-test",
            password_salt="unused-in-route-test",
            role=role,
            teams=teams,
        )
        return auth.create_login_session(user_id)

    def _upload(
        self,
        filename: str,
        content: bytes = b"# Test\n",
        *,
        token: str | None = None,
        csrf: str | None = None,
    ):
        file = _MemoryUpload(filename, content)
        return asyncio.run(
            uploads.upload_file(
                _request(self.editor_token if token is None else token),
                team="tian_gong",
                file=file,
                csrf_token=self.editor_csrf if csrf is None else csrf,
            )
        )

    def test_supported_files_are_independent_uploads(self) -> None:
        first = self._upload("说明.MD", b"first")
        second = self._upload("说明.MD", b"second")

        self.assertNotEqual(first["upload_id"], second["upload_id"])
        for result, expected in ((first, b"first"), (second, b"second")):
            self.assertEqual(result["status_page"], f"/uploads/{result['upload_id']}")
            record = database.get_upload(result["upload_id"])
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record["team"], "tian_gong")
            self.assertEqual(Path(record["ecs_path"]).read_bytes(), expected)

    def test_recent_upload_monitor_includes_source_failures(self) -> None:
        database.create_upload(
            upload_id="recent-monitor",
            task_id="recent-monitor-task",
            team="tian_gong",
            filename="recent.zip",
            size_bytes=12,
            ecs_path="/tmp/recent.zip",
            status="failed",
            stage="ingestion_failed",
            message="Ingestion failed",
        )
        database.upsert_source(
            upload_id="recent-monitor",
            source_identity="tian_gong/recent-monitor/failed-file.md",
            status="failed",
            error="Provider connection failed",
        )
        database.create_upload(
            upload_id="old-active-monitor",
            task_id="old-active-monitor-task",
            team="tian_gong",
            filename="old.md",
            size_bytes=12,
            ecs_path="/tmp/old.md",
            status="waiting_for_llm_wiki",
            stage="waiting_for_llm_wiki",
            message="Stale waiting upload",
        )
        with database._DB_LOCK, database._connect() as connection:
            connection.execute(
                "UPDATE uploads SET created_at = ?, updated_at = ? WHERE upload_id = ?",
                ("2020-01-01T00:00:00+00:00", "2020-01-01T00:00:00+00:00", "old-active-monitor"),
            )

        uploads_for_monitor = database.list_recent_uploads_with_sources(hours=24)
        monitored = next(item for item in uploads_for_monitor if item["upload_id"] == "recent-monitor")

        self.assertEqual(monitored["sources"][0]["source_identity"], "tian_gong/recent-monitor/failed-file.md")
        self.assertEqual(monitored["sources"][0]["error"], "Provider connection failed")
        self.assertNotIn("old-active-monitor", {item["upload_id"] for item in uploads_for_monitor})

    def test_unsupported_extension_returns_400_without_creating_upload(self) -> None:
        response = self._upload("payload.exe")

        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.body)
        self.assertEqual(payload["error"], "unsupported file type")
        self.assertIn(".pdf", payload["allowed_extensions"])
        self.assertEqual(database.list_uploads(), [])
        self.assertFalse(uploads.UPLOAD_ROOT.exists())

    def test_upload_still_requires_authentication_role_and_csrf(self) -> None:
        with self.assertRaises(HTTPException) as unauthenticated:
            self._upload("guide.md", token="", csrf="")
        self.assertEqual(unauthenticated.exception.status_code, 401)

        with self.assertRaises(HTTPException) as invalid_csrf:
            self._upload("guide.md", csrf="wrong-token")
        self.assertEqual(invalid_csrf.exception.status_code, 403)


class UploadBatchTemplateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.template = UPLOAD_TEMPLATE.read_text(encoding="utf-8")

    def test_multiple_file_controls_and_status_links_are_present(self) -> None:
        self.assertIn('accept="__UPLOAD_ACCEPT__" multiple', self.template)
        self.assertIn("const MAX_BATCH_FILES=20", self.template)
        self.assertIn("const UPLOAD_CONCURRENCY=2", self.template)
        self.assertIn("'download_failed'", self.template)
        self.assertIn("failedUploadStatuses.has(item.phase)", self.template)
        self.assertIn("data.status_page", self.template)
        self.assertIn("pollUpload(item)", self.template)
        self.assertNotIn("location.href=data.status_page", self.template)

    def test_each_upload_row_shows_worker_and_llm_wiki_progress(self) -> None:
        self.assertIn("pipelineWorker", self.template)
        self.assertIn("pipelineWiki", self.template)
        self.assertIn("sourceProgress", self.template)
        self.assertIn("item.sources=Array.isArray(data.sources)?data.sources:[]", self.template)
        self.assertIn("retry_count", self.template)

    def test_global_upload_status_is_persisted_and_refreshed_from_the_api(self) -> None:
        self.assertIn('id="global-upload-list"', self.template)
        self.assertIn('id="current-batch-summary"', self.template)
        self.assertIn("fetch('/api/uploads/recent?hours=24&limit=200')", self.template)
        self.assertIn("function refreshGlobalUploads()", self.template)
        self.assertIn("link.href='/uploads/'+encodeURIComponent", self.template)
        self.assertIn("source.error", self.template)
        self.assertIn("upload-row.ingesting", self.template)
        self.assertIn("document.createElement('details')", self.template)
        self.assertIn("row.open=ingesting||failedUploadStatuses.has(status)", self.template)
        self.assertIn("details.upload-row[open]>summary::before", self.template)

    def test_upload_token_notice_is_available_in_both_interface_languages(self) -> None:
        self.assertIn('data-i18n="uploadTokenNotice"', self.template)
        self.assertIn(
            "Each upload consumes processing tokens and may be published to the knowledge base.",
            self.template,
        )
        self.assertIn(
            "每次上传都会消耗处理 Token，并可能发布到知识库中。",
            self.template,
        )

    def test_bounded_queue_never_runs_more_than_two_workers(self) -> None:
        start = self.template.index("async function runBounded")
        end = self.template.index("function pollUpload", start)
        helper = self.template[start:end]
        program = helper + """
let active=0,maximum=0,completed=[];
(async()=>{
  await runBounded([0,1,2,3,4,5],2,async item=>{
    active+=1;maximum=Math.max(maximum,active);
    await new Promise(resolve=>setTimeout(resolve,5));
    completed.push(item);active-=1;
  });
  if(maximum!==2)throw new Error('maximum='+maximum);
  if(completed.length!==6)throw new Error('completed='+completed.length);
})().catch(error=>{console.error(error);process.exit(1)});
"""
        result = subprocess.run(
            ["node", "-e", program],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rendered_template_javascript_parses(self) -> None:
        rendered = self.template.replace("__ALLOWED_TEAMS__", '["tian_gong"]')
        rendered = rendered.replace(
            "__ROBOTS__",
            '[{"name":"tian_gong","english_name":"Tiangong","chinese_name":"天工"}]',
        )
        rendered = rendered.replace(
            "__SUPPORTED_UPLOAD_SUFFIXES__",
            json.dumps(sorted(SUPPORTED_UPLOAD_SUFFIXES)),
        )
        rendered = rendered.replace("__CSRF_TOKEN__", "test-token")
        scripts = []
        cursor = 0
        while True:
            start = rendered.find("<script>", cursor)
            if start < 0:
                break
            end = rendered.find("</script>", start)
            self.assertGreater(end, start)
            scripts.append(rendered[start + len("<script>") : end])
            cursor = end + len("</script>")
        self.assertTrue(scripts)
        for script in scripts:
            result = subprocess.run(
                ["node", "-e", f"new Function({json.dumps(script)})"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_incomplete_scan_is_not_counted_as_suspicious_content(self) -> None:
        template = UPLOAD_STATUS_TEMPLATE.read_text(encoding="utf-8")
        start = template.index("function securityWarningSummary")
        end = template.index("function updateSecurityWarnings", start)
        helper = template[start:end]
        program = helper + """
const summary=securityWarningSummary([
  {categories:['scan_incomplete_size']},
  {categories:['instruction_override']},
  {categories:['scan_incomplete_encoding','prompt_exfiltration']},
]);
if(!summary.hasIncompleteWarning)throw new Error('missing incomplete warning');
if(summary.suspiciousCount!==2)throw new Error('suspicious='+summary.suspiciousCount);
"""
        result = subprocess.run(
            ["node", "-e", program],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_status_template_javascript_parses_and_download_failure_is_terminal(self) -> None:
        template = UPLOAD_STATUS_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn(
            "['completed','failed','partially_failed','download_failed','sources_removed']",
            template,
        )
        start = template.index("<script>") + len("<script>")
        end = template.index("</script>", start)
        script = template[start:end].replace("__UPLOAD_ID__", "upload-test")
        result = subprocess.run(
            ["node", "-e", f"new Function({json.dumps(script)})"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
