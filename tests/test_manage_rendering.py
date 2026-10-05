"""Execute the real management-page error renderer, including its shared helper scope."""
import json
import re
import subprocess
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / 'ecs/app/templates/manage.html'


def render_chat_robot_error(page: str) -> subprocess.CompletedProcess:
    script = re.search(r'<script>(.*?)</script>', page, re.S)[1]
    shared = script.split('function buildItem(')[0].replace('__CAN_DELETE__', 'false')
    renderer = script[script.index('async function loadChatRobots()'):script.index('function renderChatRobotsTable()')]
    harness = '''
const assert = require('node:assert/strict');
const vm = require('node:vm');
const nodes = new Map();
const context = vm.createContext({
  document: {getElementById(id) {if (!nodes.has(id)) nodes.set(id, {value:'en', innerHTML:''}); return nodes.get(id);}},
  localStorage: {getItem() {return 'en';}},
  console: {error() {}},
  fetch: async () => ({ok:false, json:async () => ({error:'<img src=x onerror=alert(1)> & unavailable'})})
});
vm.runInContext(SHARED + '\\n' + RENDERER, context);
(async () => {
  await vm.runInContext('loadChatRobots()', context);
  const rendered = nodes.get('chat-robots-tbody').innerHTML;
  assert.ok(rendered.includes('&lt;img src=x onerror=alert(1)&gt; &amp; unavailable'), rendered);
  assert.ok(!rendered.includes('<img'), rendered);
})().catch(error => {console.error(error); process.exitCode=1;});
'''
    harness = 'const SHARED=' + json.dumps(shared) + ';const RENDERER=' + json.dumps(renderer) + ';\n' + harness
    return subprocess.run(['node', '-e', harness], capture_output=True, text=True, timeout=10)


def test_chat_robot_error_rendering_uses_shared_html_escaping():
    result = render_chat_robot_error(TEMPLATE.read_text())
    assert result.returncode == 0, result.stderr
