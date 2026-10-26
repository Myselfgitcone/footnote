import pytest
from fastapi.testclient import TestClient

from footnote import api
from footnote.ask import Plan
from footnote.settings import Settings
from footnote.store import LocalStore


class FakeAsker:
    def __init__(self, fail=False):
        self.questions = []
        self.fail = fail

    def invoke(self, state):
        self.questions.append(state["question"])
        if self.fail:
            raise RuntimeError("Anthropic is down")
        return {
            "answer": "Apple's fiscal 2024 revenue was $391,035 million [F1].",
            "sources": [{"id": "F1", "kind": "figure", "ticker": "AAPL", "value": 391e9, "internal": "x"}],
            "plan": Plan(in_scope=True, tickers=["AAPL"], metrics=["revenue"]),
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    asker = FakeAsker()
    monkeypatch.setattr(
        api,
        "_state",
        {"settings": Settings(daily_question_limit=2), "store": LocalStore(tmp_path), "asker": asker},
    )
    return TestClient(api.app), asker


def test_answers_with_cited_sources(client):
    http, _ = client
    response = http.post("/ask", json={"question": "What was Apple's revenue in 2024?"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"].endswith("[F1].") and body["cached"] is False
    assert body["sources"] == [{"id": "F1", "kind": "figure", "ticker": "AAPL", "value": 391e9}]
    assert body["plan"]["tickers"] == ["AAPL"]


def test_the_same_question_is_answered_from_cache(client):
    http, asker = client
    http.post("/ask", json={"question": "What was Apple's revenue in 2024?"})
    again = http.post("/ask", json={"question": "  what was apple's   revenue in 2024? "})
    assert again.json()["cached"] is True
    assert len(asker.questions) == 1


def test_new_questions_stop_at_the_daily_limit(client):
    http, _ = client
    assert http.post("/ask", json={"question": "one?"}).status_code == 200
    assert http.post("/ask", json={"question": "two?"}).status_code == 200
    assert http.post("/ask", json={"question": "three?"}).status_code == 429
    assert http.post("/ask", json={"question": "one?"}).status_code == 200  # cached answers still flow


def test_questions_must_be_a_sensible_length(client):
    http, _ = client
    assert http.post("/ask", json={"question": "x"}).status_code == 422
    assert http.post("/ask", json={"question": "x" * 301}).status_code == 422


def test_a_failure_is_a_clean_error_and_not_counted(client, monkeypatch):
    http, _ = client
    api._state["asker"] = FakeAsker(fail=True)
    response = http.post("/ask", json={"question": "anything?"})
    assert response.status_code == 502 and "try again" in response.json()["detail"]
    api._state["asker"] = FakeAsker()
    assert http.post("/ask", json={"question": "aa?"}).status_code == 200
    assert http.post("/ask", json={"question": "bb?"}).status_code == 200


def test_health(client):
    http, _ = client
    assert http.get("/health").json() == {"ok": True}


def test_the_lambda_handler_speaks_function_url_events(client):
    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": "/health",
        "rawQueryString": "",
        "headers": {"host": "x.lambda-url.us-east-2.on.aws"},
        "requestContext": {
            "http": {"method": "GET", "path": "/health", "sourceIp": "1.2.3.4", "protocol": "HTTP/1.1"},
            "domainName": "x.lambda-url.us-east-2.on.aws",
            "stage": "$default",
            "requestId": "r",
        },
        "isBase64Encoded": False,
    }
    response = api.handler(event, None)
    assert response["statusCode"] == 200 and '"ok":true' in response["body"]
