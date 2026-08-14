from __future__ import annotations

import json
import re
from pathlib import Path

from ecs.app.config import ROOT_PATH

_TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates"
_ROOT_RELATIVE_ATTRIBUTE = re.compile(
    r"(?P<prefix>\b(?:href|src|action|data-account-return)\s*=\s*)"
    r"(?P<quote>[\"'])(?P<path>/(?!/)[^\"']*)"
)


def rooted_path(path: str) -> str:
    """Return a same-origin URL path beneath the configured application prefix."""
    if not ROOT_PATH or not path.startswith("/") or path.startswith("//"):
        return path
    if path == ROOT_PATH or path.startswith(f"{ROOT_PATH}/"):
        return path
    return f"{ROOT_PATH}{path}"


def _root_browser_script() -> str:
    encoded_root = json.dumps(ROOT_PATH).replace("<", "\\u003c")
    return f"""<script>
(function () {{
  'use strict';
  const appRoot = {encoded_root};
  function appUrl(path) {{
    if (!appRoot || typeof path !== 'string' || !path.startsWith('/') || path.startsWith('//')) return path;
    if (path === appRoot || path.startsWith(appRoot + '/')) return path;
    return appRoot + path;
  }}
  window.__APP_ROOT__ = appRoot;
  window.appUrl = appUrl;
  if (appRoot && typeof window.fetch === 'function') {{
    const originalFetch = window.fetch.bind(window);
    window.fetch = function (resource, options) {{
      if (typeof resource === 'string') resource = appUrl(resource);
      return originalFetch(resource, options);
    }};
  }}
}})();
</script>"""


def render_template(name: str, *, include_background: bool = False) -> str:
    """Load an HTML template and make its browser URLs ROOT_PATH-aware."""
    content = (_TEMPLATE_ROOT / name).read_text(encoding="utf-8")
    if include_background and name != "bg_graph.html" and "</body>" in content:
        background = (_TEMPLATE_ROOT / "bg_graph.html").read_text(encoding="utf-8")
        content = content.replace("</body>", background + "\n</body>")

    if ROOT_PATH:
        content = _ROOT_RELATIVE_ATTRIBUTE.sub(
            lambda match: (
                f"{match.group('prefix')}{match.group('quote')}"
                f"{rooted_path(match.group('path'))}"
            ),
            content,
        )
    if "<head>" in content:
        content = content.replace("<head>", "<head>\n" + _root_browser_script(), 1)
    return content
