"""Reviewed synthetic contrasts; no model-generated label is accepted unchecked."""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from ff_agent.safety import assess_request

CASES=json.loads((Path(__file__).resolve().parents[2]/"docs/cases/sprint-adversarial.json").read_text(encoding="utf-8"))["variants"]


@pytest.mark.parametrize("case",CASES,ids=lambda case:case["id"])
def test_adversarial_hold_label(case):
    finding=assess_request(case["text"])
    assert bool(finding and finding["hold"]) is case["expect_hold"]


@pytest.mark.parametrize("case",CASES,ids=lambda case:case["id"])
def test_adversarial_never_confirms_or_changes_money(tmp_path,monkeypatch,case):
    app=create_app(tmp_path/"adversarial.db");bank=app.state.bank
    def financial_snapshot():
        with bank.transaction() as db:
            return [tuple(row) for row in db.execute("SELECT * FROM accounts ORDER BY id")],[tuple(row) for row in db.execute("SELECT * FROM transactions ORDER BY id")]
    before=financial_snapshot()
    def forbidden(*args,**kwargs):
        raise AssertionError("Chat must never call financial confirmation or OTP")
    monkeypatch.setattr(bank,"confirm",forbidden);monkeypatch.setattr(bank,"verify",forbidden)
    with TestClient(app) as client:
        response=client.post("/api/session",json={})
        client.headers["X-CSRF-Token"]=response.json()["csrf"]
        result=client.post("/api/chat",json={"text":case["text"],"request_key":case["id"]})
        assert result.status_code==200
        assert financial_snapshot()==before
        if case["expect_hold"]:
            assert client.get("/api/state").json()["safety_hold"] is not None
