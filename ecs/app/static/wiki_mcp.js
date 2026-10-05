(() => {
  const select = document.getElementById('mcp-language');
  let preferred = navigator.language || 'en';
  try { preferred = localStorage.getItem('agent1_ui_language') || preferred; } catch {}
  select.value = preferred.startsWith('zh') ? 'zh' : 'en';
  let ready = null;
  function render() {
    const language = select.value;
    document.documentElement.lang = language === 'zh' ? 'zh-CN' : 'en';
    document.querySelectorAll('[data-lang]').forEach(el => { el.hidden = el.dataset.lang !== language; });
    document.title = language === 'zh' ? '连接 AI 助手 · Wiki MCP' : 'Connect your AI · Wiki MCP';
    document.getElementById('mcp-status').textContent = ready === true
      ? (language === 'zh' ? '知识库连接已就绪。' : 'Wiki connection is ready.')
      : (language === 'zh' ? '知识库连接准备中或暂时不可用，连接包仍可下载。' : 'The wiki connection is being prepared or is temporarily unavailable. You can still download the connector.');
  }
  select.addEventListener('change', render);
  render();
  fetch('/wiki-mcp/status').then(response => response.ok ? response.json() : Promise.reject()).then(data => { ready = data.ready === true; render(); }).catch(() => { ready = false; render(); });
})();
