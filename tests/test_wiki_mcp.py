import asyncio
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock
from zipfile import ZipFile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager

from shared.public_wiki import public_page
from worker.wiki_content import technical_markdown, excerpt, localize
from worker.wiki_mcp import read_page, search_pages
from ecs.app import wiki_mcp
from ecs.app.routes import mcp_setup


@pytest.mark.parametrize('page', ['../secret.md', '/etc/passwd', 'queries/customer.md', '.hidden.md', 'log.md', 'raw/source.md', 'a/../manual.md', 'a\\manual.md', 'report.txt'])
def test_public_path_denies_non_wiki_data(tmp_path, page):
    assert public_page(tmp_path, page) is None


def test_public_path_rejects_parent_symlinks_and_oversized_files(tmp_path):
    real = tmp_path / 'real'; real.mkdir()
    (real / 'spec.md').write_text('# Spec\nPayload 5 kg')
    (tmp_path / 'alias').symlink_to(real, target_is_directory=True)
    assert public_page(tmp_path, 'alias/spec.md') is None
    assert public_page(tmp_path, 'real/spec.md') is not None
    (tmp_path / 'large.md').write_bytes(b'x' * (2 * 1024 * 1024 + 1))
    assert public_page(tmp_path, 'large.md') is None


CONTENT = '# Walker\n## 产品定位\nLeading product for everyone\n### 宣传\nBUY NOW\n## 技术规格\n负载 5 kg\n## Safety\nDo not exceed 5 kg.\n'


def test_filter_keeps_technical_facts_and_original_line_citations(tmp_path):
    (tmp_path / 'manual.md').write_text(CONTENT)
    page = read_page(tmp_path, 'manual.md')
    assert 'BUY NOW' not in page['content']
    assert 'Leading' not in page['content']
    assert '负载 5 kg' in page['content'] and 'Do not exceed 5 kg' in page['content']
    assert page['line_numbers'][page['content'].splitlines().index('负载 5 kg')] == 7
    assert page['filtered_sections'] == ['产品定位']
    assert page['citation'] == 'wiki/manual.md#L1'
    assert (tmp_path / 'manual.md').read_text() == CONTENT


def test_filter_ignores_headings_inside_code_and_preserves_features():
    source = '---\ntitle: secret metadata\n---\n# Features\nReach 1m\n```md\n## 产品定位\nexample code\n```\n## Product positioning\nmarketing\n## Limitations\nNo waterproofing\n'
    output, _ = technical_markdown(source)
    assert 'secret metadata' not in output
    assert 'example code' in output and 'Reach 1m' in output
    assert 'marketing' not in output and 'No waterproofing' in output


def test_read_pagination_and_character_limit(tmp_path):
    (tmp_path / 'page.md').write_text('\n'.join(f'line {i}' for i in range(1, 201)))
    page = read_page(tmp_path, 'page.md', 81, 80)
    assert page['start_line'] == 81 and page['end_line'] == 160 and page['next_line'] == 161
    assert page['truncated'] is True
    with pytest.raises(ValueError):
        read_page(tmp_path, 'page.md', 0)
    long = excerpt('x' * 15000, 1, 80, 12000)
    assert len(long['content']) == 12000 and long['next_line'] == 1


class Translator:
    def complete(self, system, user):
        self.system = system
        self.input = json.loads(user)
        return json.dumps({'language': 'en', 'translations': [{'index': 0, 'text': 'Payload 5 kg'}]})


def test_translation_preserves_originals_and_citations():
    items = [{'content': '负载 5 kg', 'citation': 'wiki/manual.md#L7'}]
    provider = Translator()
    status = localize(items, provider, 'auto', 'What is the payload?')
    assert status['language'] == 'en' and status['translation_status'] == 'machine_translated'
    assert items[0]['content'] == '负载 5 kg'
    assert items[0]['translated_content'] == 'Payload 5 kg'
    assert items[0]['citation'] == 'wiki/manual.md#L7'
    assert 'untrusted data' in provider.system


def test_translation_failure_returns_original_without_fabricated_translation():
    class Bad:
        def complete(self, *args): return 'not json'
    items = [{'content': '负载 5 kg'}]
    assert localize(items, Bad(), 'en')['translation_status'] == 'unavailable'
    assert 'translated_content' not in items[0]
    assert localize(items, Bad(), 'auto', '负载是多少')['translation_status'] == 'original'


def test_search_filters_private_and_promotional_pages_before_return(tmp_path, monkeypatch):
    from worker import wiki_mcp as worker_mcp
    (tmp_path / 'manual.md').write_text(CONTENT)
    (tmp_path / 'queries').mkdir()
    (tmp_path / 'queries/private.md').write_text('PRIVATE conversation')
    def graph(**kwargs):
        assert kwargs['public_only'] is True
        return {'loaded_evidence': [{'path': 'manual.md', 'content': CONTENT}, {'path': 'queries/private.md', 'content': 'PRIVATE'}],
                'evidence_sufficient': True, 'answer_user': 'PRIVATE PROMPT'}
    monkeypatch.setattr(worker_mcp, '_run_graph', graph)
    result = search_pages(tmp_path, Translator(), 'Payload?', 'all', 'en')
    assert len(result['evidence']) == 1
    assert 'PRIVATE' not in json.dumps(result)
    assert 'BUY NOW' not in json.dumps(result)
    assert result['translation_status'] == 'machine_translated'


def test_limits_bound_history_and_do_not_extend_window_on_rejection(monkeypatch):
    limits = wiki_mcp.Limits()
    clock = [0]
    monkeypatch.setattr(wiki_mcp.time, 'monotonic', lambda: clock[0])
    for _ in range(10): assert limits.allow('client')
    assert not limits.allow('client')
    clock[0] = 61
    assert limits.allow('client')
    clock[0] = 4000
    assert limits.allow('other')
    assert 'client' not in limits.history


def test_real_mcp_http_initialize_list_call_and_offline(monkeypatch):
    monkeypatch.setattr(wiki_mcp, 'http_limits', wiki_mcp.Limits())
    monkeypatch.setattr(wiki_mcp, 'search_limits', wiki_mcp.Limits())
    monkeypatch.setattr(wiki_mcp, 'robots', lambda: [{'id': 'walker', 'name_zh': '行者', 'name_en': 'Walker'}])
    gateway = AsyncMock(return_value={'status': 'ok', 'data': {'evidence': [{'content': 'Payload 5 kg'}]}})
    monkeypatch.setattr(wiki_mcp.gateway, 'command', gateway)
    monkeypatch.setattr(wiki_mcp.gateway, 'wiki_mcp_ready', True)
    @asynccontextmanager
    async def lifespan(app):
        async with wiki_mcp.mcp.session_manager.run(): yield
    app = FastAPI(lifespan=lifespan)
    app.mount('/mcp', wiki_mcp.transport)
    headers = {'Accept': 'application/json, text/event-stream'}
    with TestClient(app) as client:
        def rpc(method, params={}, id=1):
            return client.post('/mcp/', json={'jsonrpc': '2.0', 'id': id, 'method': method, 'params': params}, headers=headers)
        init = rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
        assert init.status_code == 200, init.text
        listing = rpc('tools/list').json()['result']['tools']
        assert {t['name'] for t in listing} == {'list_robots', 'search_wiki', 'read_wiki_page'}
        assert all(t['annotations']['readOnlyHint'] for t in listing)
        result = rpc('tools/call', {'name': 'search_wiki', 'arguments': {'question': 'payload', 'language': 'en'}})
        assert 'structuredContent' in result.json()['result'], result.text
        assert result.json()['result']['structuredContent']['evidence'][0]['content'] == 'Payload 5 kg'
        gateway.assert_awaited_once()
        result = rpc('tools/call', {'name': 'read_wiki_page', 'arguments': {'page_id': 'a.md', 'max_lines': 1000}})
        assert result.json()['result']['isError'] is True
        gateway.side_effect = ConnectionError('PRIVATE INTERNAL PATH')
        error = rpc('tools/call', {'name': 'read_wiki_page', 'arguments': {'page_id': 'a.md'}})
        assert error.json()['result']['isError'] is True
        assert 'PRIVATE' not in error.text
        assert client.post('/mcp/', content='x' * 17000, headers=headers).status_code == 413


def test_download_contains_only_connector_and_prefixed_public_url(monkeypatch):
    monkeypatch.setattr(mcp_setup, 'PUBLIC_BASE_URL', 'https://wiki.example/v1/faq-platform')
    monkeypatch.setattr(mcp_setup, 'rooted_path', lambda value: '/v1/faq-platform' + value)
    app = FastAPI(); app.include_router(mcp_setup.router)
    with TestClient(app) as client:
        response = client.get('/wiki-mcp/download')
        assert response.status_code == 200
        with ZipFile(BytesIO(response.content)) as archive:
            assert set(archive.namelist()) == {'wiki_mcp.py', 'README.md', 'mcp-config.json'}
            config = json.loads(archive.read('mcp-config.json'))
            assert config['mcpServers']['ubtech-wiki']['env']['WIKI_MCP_URL'] == 'https://wiki.example/v1/faq-platform/mcp/'
        assert 'Wiki MCP' in client.get('/wiki-mcp').text


def test_real_graph_english_query_retrieves_chinese_technical_evidence(tmp_path):
    root = tmp_path / 'wiki'; (root / 'entities').mkdir(parents=True)
    (root / 'entities/payload.md').write_text('---\ntitle: 机械臂负载\ntags: [负载]\n---\n' + CONTENT)
    (root / 'queries').mkdir()
    (root / 'queries/private.md').write_text('# 负载\nprivate question history')
    class Provider(Translator):
        timeout = 10
        def complete(self, system, user):
            if 'Intent Planner' in system:
                assert 'Chinese keyword translations' in system
                return json.dumps({'scope_analysis': {'active_scope': 'all', 'relation': 'in_scope', 'reason': 'explicit task'},
                    'standalone_question': 'What is the payload?', 'intent': 'concept',
                    'preferred_abstraction': 'sdk_or_module', 'search_queries': ['机械臂 负载']})
            if 'Translate technical' in system:
                return super().complete(system, user)
            assert 'private question history' not in user
            return json.dumps({'primary_solution': 'Payload documentation', 'selected_pages': ['entities/payload.md'],
                               'direct_answer_plan': 'Cite payload', 'evidence_sufficient': True})
    result = search_pages(root, Provider(), 'What is the payload?', 'all', 'en')
    assert result['evidence'][0]['page_id'] == 'entities/payload.md'
    assert '负载 5 kg' in result['evidence'][0]['content']
    assert 'Leading product' not in result['evidence'][0]['content']
    assert result['evidence'][0]['translated_content'] == 'Payload 5 kg'
    assert result['translation_status'] == 'machine_translated'


def test_english_question_with_chinese_robot_name_still_gets_translation():
    items = [{'content': '负载 5 kg'}]
    assert localize(items, Translator(), 'auto', "What is 天工's payload?")['language'] == 'en'
    assert items[0]['translated_content'] == 'Payload 5 kg'
