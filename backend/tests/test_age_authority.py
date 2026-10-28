from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from ff_agent.api import create_app
from tests.demo_assessment import consent_test_profile


@pytest.mark.parametrize("text", [
    "我年纪大了，挂失日常账户银行卡",
    "我是退休用户，请挂失日常账户银行卡",
    "我年纪大了，日常账户买演示产品甲100元",
    "我是退休用户，日常账户买演示产品甲100元",
])
def test_age_does_not_bypass_red_confirmation(text, tmp_path, monkeypatch):
    monkeypatch.setenv("FF_PLANNER_MODE", "rules")
    app = create_app(db_path=str(tmp_path / "bank.sqlite3"))
    # Explicit test-only prerequisite for purchase cases; age never supplies consent.
    if "演示产品" in text:
        consent_test_profile(app.state.bank)

    with TestClient(app) as client:
        response = client.post("/api/session", json={"user": "demo"})
        assert response.status_code == 200, response.text
        client.headers.update({"X-CSRF-Token": response.json()["csrf"]})

        def state():
            response = client.get("/api/state")
            assert response.status_code == 200, response.text
            return response.json()

        accounts_before = {a["id"]:a["balance_cents"] for a in state()["accounts"]}
        cards_before = deepcopy(app.state.bank.list_cards("demo"))
        assert app.state.bank.wealth("demo")["positions"] == []

        def assert_unchanged(expected_status, operation_id):
            current = state()
            assert {a["id"]:a["balance_cents"] for a in current["accounts"]} == accounts_before
            assert app.state.bank.list_cards("demo") == cards_before
            assert app.state.bank.wealth("demo")["positions"] == []
            rows = [op for op in current["operations"]
                    if op["id"] == operation_id]
            assert len(rows) == 1
            assert rows[0]["status"] == expected_status
            assert rows[0]["status"] != "SUCCEEDED"

        response = client.post("/api/chat", json={
            "text": text,
            "request_key": "age-boundaries",
        })
        assert response.status_code == 200, response.text
        operations = [card["operation"]
                      for card in response.json()["cards"]
                      if card["type"] == "operation"]
        suggestion = (
            text.replace("挂失日常账户银行卡", "挂失日常账户的银行卡")
            if "挂失" in text else
            text.replace("日常账户买演示产品甲100元",
                         "用日常账户申购100元演示产品甲")
        )
        assert len(operations) == 1, (
            f"文本未产生唯一 operation，不可用于此回归测试：{text!r}；"
            f"最小同义候选（尚未验证）：{suggestion!r}；响应：{response.text}"
        )
        operation = operations[0]
        assert operation["risk"] == "RED"
        assert operation["status"] == "WAITING_CONFIRMATION"
        operation_id = operation["id"]
        base = f"/api/operations/{operation_id}"
        assert_unchanged("WAITING_CONFIRMATION", operation_id)

        # 空请求可能被请求校验拒绝；不猜测具体错误码。
        response = client.post(f"{base}/verify")
        assert response.status_code != 200, response.text
        assert_unchanged("WAITING_CONFIRMATION", operation_id)

        # 同时覆盖具有合法请求结构、但尚未确认的验证请求。
        response = client.post(f"{base}/verify", json={"code": "000000"})
        assert response.status_code != 200, response.text
        assert_unchanged("WAITING_CONFIRMATION", operation_id)

        response = client.post(f"{base}/confirm",json={"verified":True})
        assert response.status_code == 422,response.text
        assert_unchanged("WAITING_CONFIRMATION",operation_id)
        response = client.post(f"{base}/confirm",json={})
        assert response.status_code == 200, response.text
        assert_unchanged("WAITING_VERIFICATION", operation_id)

        response = client.post(f"{base}/verify", json={"code": "000000"})
        assert response.status_code == 403, response.text
        assert_unchanged("WAITING_VERIFICATION", operation_id)
