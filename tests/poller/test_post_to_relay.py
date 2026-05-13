"""Unit tests for poller.post_to_relay — happy paths + failure modes (None return)."""

from __future__ import annotations

import requests
import requests_mock as rm_module

from poller.poll_geeknews import FeedEntry, post_to_relay

_RELAY_URL = "http://127.0.0.1:8080/webhook/geeknews"
_SECRET = "test-secret"


def _make_entry() -> FeedEntry:
    return FeedEntry(
        title="Sample Title",
        url="https://news.hada.io/topic?id=1",
        guid="g-1",
        excerpt="hello",
        published_at=None,
    )


def test_post_to_relay_matched(requests_mock: rm_module.Mocker) -> None:
    requests_mock.post(
        _RELAY_URL,
        status_code=200,
        json={"status": "matched", "openclaw_status": 200},
    )
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is not None
    assert resp.status == "matched"
    assert resp.openclaw_status == 200
    assert resp.http_status == 200

    # 헤더와 본문 검증.
    last = requests_mock.last_request
    assert last is not None
    assert last.headers["X-Webhook-Secret"] == _SECRET
    body = last.json()
    assert body["title"] == "Sample Title"
    assert body["guid"] == "g-1"
    assert body["url"] == "https://news.hada.io/topic?id=1"


def test_post_to_relay_no_reply(requests_mock: rm_module.Mocker) -> None:
    requests_mock.post(
        _RELAY_URL,
        status_code=200,
        json={"status": "no_reply", "openclaw_status": 200},
    )
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is not None
    assert resp.status == "no_reply"
    assert resp.openclaw_status == 200


def test_post_to_relay_returns_none_on_401(requests_mock: rm_module.Mocker) -> None:
    requests_mock.post(_RELAY_URL, status_code=401, json={"error": "auth"})
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret="wrong")
    assert resp is None


def test_post_to_relay_returns_none_on_5xx(requests_mock: rm_module.Mocker) -> None:
    requests_mock.post(_RELAY_URL, status_code=502, json={"error": "openclaw"})
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is None


def test_post_to_relay_returns_none_on_connection_error(
    requests_mock: rm_module.Mocker,
) -> None:
    requests_mock.post(_RELAY_URL, exc=requests.exceptions.ConnectionError)
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is None


def test_post_to_relay_returns_none_on_invalid_json(requests_mock: rm_module.Mocker) -> None:
    requests_mock.post(_RELAY_URL, status_code=200, text="not-json-body")
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is None


def test_post_to_relay_returns_none_on_missing_status(
    requests_mock: rm_module.Mocker,
) -> None:
    requests_mock.post(_RELAY_URL, status_code=200, json={"openclaw_status": 200})
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is None


def test_post_to_relay_returns_none_on_unknown_status_value(
    requests_mock: rm_module.Mocker,
) -> None:
    requests_mock.post(_RELAY_URL, status_code=200, json={"status": "bogus"})
    resp = post_to_relay(_make_entry(), relay_url=_RELAY_URL, secret=_SECRET)
    assert resp is None
