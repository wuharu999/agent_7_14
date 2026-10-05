from io import BytesIO
from pathlib import Path
import html
import json
from urllib.parse import urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from ecs.app.config import PUBLIC_BASE_URL
from ecs.app.gateway import gateway
from ecs.app.web_paths import render_template, rooted_path

router = APIRouter()
CONNECTOR = Path(__file__).resolve().parents[3] / 'integrations' / 'wiki_mcp'


def endpoint(request: Request) -> str:
    base = urlsplit(PUBLIC_BASE_URL or str(request.base_url))
    return f'{base.scheme}://{base.netloc}{rooted_path("/mcp/")}'


@router.get('/wiki-mcp', response_class=HTMLResponse)
async def setup(request: Request):
    page = render_template('wiki_mcp.html', include_background=True)
    return HTMLResponse(page.replace('__MCP_URL__', html.escape(endpoint(request), quote=True)))


@router.get('/wiki-mcp/download')
async def download(request: Request):
    config = {'mcpServers': {'ubtech-wiki': {'command': 'uv',
              'args': ['run', '--script', 'REPLACE_WITH_ABSOLUTE_PATH/wiki_mcp.py'],
              'env': {'WIKI_MCP_URL': endpoint(request)}}}}
    data = BytesIO()
    with ZipFile(data, 'w', ZIP_DEFLATED) as archive:
        for name in ('wiki_mcp.py', 'README.md'):
            archive.writestr(name, (CONNECTOR / name).read_bytes())
        archive.writestr('mcp-config.json', json.dumps(config, ensure_ascii=False, indent=2))
    return Response(data.getvalue(), media_type='application/zip',
                    headers={'Content-Disposition': 'attachment; filename="ubtech-wiki-mcp.zip"', 'Cache-Control': 'no-store'})


@router.get('/wiki-mcp/status')
async def status():
    return {'ready': gateway.online and gateway.wiki_mcp_ready}
