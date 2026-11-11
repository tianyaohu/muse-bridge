"""since is absolute time, and a null company is not the word none."""

from __future__ import annotations

import pytest

from muse_bridge.auth.principal import FULL_SCOPES, Principal
from muse_bridge.context import use_request_context
from muse_bridge.domain import list_items, list_recent_finds, pull_inbox
from muse_bridge.tools.items import list_items as list_items_tool
from muse_bridge.tools.jobs import list_recent_finds as list_recent_finds_tool


def _principal(user_id: str) -> Principal:
    return Principal(user_id=user_id, auth_method="api_key", scopes=FULL_SCOPES, key_id="test")


def _find(record_id: str, received_at: str, **extra: object) -> dict:
    row = {
        "id": record_id,
        "kind": "job_find",
        "type": "job_find",
        "title": record_id,
        "company": "Acme",
        "url": f"https://example.com/{record_id}",
        "received_at": received_at,
    }
    row.update(extra)
    return row


def test_since_compares_absolute_time_not_text(bridge):
    store = bridge.state.store
    store.append("alice", _find("noon-utc", "2026-11-11T12:00:00+00:00"))
    store.append("alice", _find("two-utc", "2026-11-11T14:00:00+00:00"))
    store.append("alice", _find("naive", "2026-11-11T12:30:00", title="Naive"))

    # 08:00-05:00 is 13:00Z. Noon UTC is earlier even though the text sorts later.
    after_eastern = store.list_records("alice", since="2026-11-11T08:00:00-05:00")
    assert [row["id"] for row in after_eastern] == ["two-utc"]

    after_noon = store.list_records("alice", since="2026-11-11T12:00:00Z")
    assert [row["id"] for row in after_noon] == ["two-utc", "naive"]

    # Equal instant is exclusive.
    assert store.list_records("alice", since="2026-11-11T14:00:00+00:00") == []

    naive_cutoff = store.list_records("alice", since="2026-11-11T12:45:00")
    assert [row["id"] for row in naive_cutoff] == ["two-utc"]


def test_unparseable_received_at_is_omitted_only_when_since_is_set(bridge):
    store = bridge.state.store
    store.append("alice", _find("dated", "2026-11-11T15:00:00Z"))
    store.append("alice", _find("blank-stamp", ""))
    null_stamp = _find("null-stamp", "2026-11-11T15:00:00Z")
    null_stamp["received_at"] = None
    store.append("alice", null_stamp)

    bounded = store.list_records("alice", since="2026-11-11T14:00:00Z")
    assert [row["id"] for row in bounded] == ["dated"]
    assert [row["id"] for row in store.list_records("alice")] == ["dated", "blank-stamp", "null-stamp"]
    assert store.list_records("alice", since="") == store.list_records("alice", since="   ")


def test_unparseable_since_is_rejected(bridge, client):
    store = bridge.state.store
    store.append("alice", _find("dated", "2026-11-11T15:00:00Z"))
    with pytest.raises(ValueError, match="invalid_since"):
        store.list_records("alice", since="yesterday")

    issued = bridge.state.key_store.create_key("alice")
    response = client.get(
        "/api/inbox",
        params={"since": "yesterday"},
        headers={"Authorization": f"Bearer {issued.token}"},
    )
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_since"}

    blank = client.get("/api/inbox", headers={"Authorization": f"Bearer {issued.token}"})
    assert blank.status_code == 200
    assert blank.json()["count"] == 1

    listed = list_items(store, "alice", since="yesterday")
    assert listed == {"count": 0, "items": [], "error": "invalid_since"}
    with use_request_context(_principal("alice"), store, bridge.state.settings):
        assert list_items_tool(since="not-a-time")["error"] == "invalid_since"


def test_pull_orders_by_absolute_time(bridge):
    store = bridge.state.store
    store.append("alice", _find("eastern", "2026-11-11T08:00:00-05:00"))
    store.append("alice", _find("noon", "2026-11-11T12:00:00+00:00"))
    store.append("alice", _find("undated", "not-a-time"))
    pulled = pull_inbox(store, "alice")
    assert [row["id"] for row in pulled["records"]] == ["noon", "eastern", "undated"]


def test_null_company_does_not_match_the_word_none(bridge):
    store = bridge.state.store
    store.append("alice", _find("late", "2026-11-11T12:00:00Z", title="Late", company=None))
    store.append(
        "alice",
        {
            "id": "absent",
            "kind": "job_find",
            "type": "job_find",
            "title": "Absent",
            "url": "https://example.com/absent",
            "received_at": "2026-11-11T12:01:00Z",
        },
    )
    store.append("alice", _find("northern", "2026-11-11T12:02:00Z", title="Northern None", company="Northern None"))
    store.append("alice", _find("acme", "2026-11-11T12:03:00Z", title="Acme", company="Acme"))

    none = list_recent_finds(store, "alice", company="none")
    assert [row["title"] for row in none["finds"]] == ["Northern None"]
    acme = list_recent_finds(store, "alice", company="acme")
    assert [row["title"] for row in acme["finds"]] == ["Acme"]
    with use_request_context(_principal("alice"), store, bridge.state.settings):
        tool = list_recent_finds_tool(company="none")
    assert [row["title"] for row in tool["finds"]] == ["Northern None"]


def test_delete_by_id_does_not_treat_null_as_the_word_none(bridge):
    store = bridge.state.store
    store.append("alice", _find("word", "2026-11-11T12:00:00Z", id="None"))
    store.append("alice", _find("null-id", "2026-11-11T12:01:00Z", id=None))
    removed = store.delete_records("alice", ids={"None"})
    assert removed == 1
    left = store.list_records("alice")
    assert len(left) == 1
    assert left[0]["id"] is None
