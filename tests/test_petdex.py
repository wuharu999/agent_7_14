from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from ecs.app.main import app

client = TestClient(app)

def test_petdex_assets_serve():
    # Verify styles.css serves correctly
    res_css = client.get("/petdex/styles.css")
    assert res_css.status_code == 200
    assert "petdex-companion" in res_css.text

    # Verify petdex.js serves correctly
    res_js = client.get("/petdex/petdex.js")
    assert res_js.status_code == 200
    assert "PetDexCompanion" in res_js.text

    # Verify sheet images serve correctly
    res_sheet = client.get("/petdex/assets/thinking-sheet.png")
    assert res_sheet.status_code == 200
    assert len(res_sheet.content) > 0
