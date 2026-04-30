"""Smoke test for IMP-332 blockedBy filter logic."""
import pytest
from unittest.mock import AsyncMock, patch
from autosymph.linear_client import LinearClient, IssueRef, LinearIssue


@pytest.mark.asyncio
async def test_blocked_issue_filtered_out():
    client = LinearClient(api_key="dummy", project_name="test")
    client._project_id = "proj-1"

    fake_response = {
        "issues": {
            "nodes": [
                {
                    "id": "uuid-A",
                    "identifier": "TEST-1",
                    "title": "A",
                    "priority": 1,
                    "description": "",
                    "assignee": None,
                    "state": {"name": "Ready"},
                    "labels": {"nodes": []},
                    "inverseRelations": {"nodes": []},
                },
                {
                    "id": "uuid-B",
                    "identifier": "TEST-2",
                    "title": "B blocked by A",
                    "priority": 1,
                    "description": "",
                    "assignee": None,
                    "state": {"name": "Ready"},
                    "labels": {"nodes": []},
                    "inverseRelations": {
                        "nodes": [
                            {"type": "blocks", "issue": {"identifier": "TEST-1", "state": {"name": "Implementing"}}}
                        ]
                    },
                },
            ]
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=fake_response)):
        result = await client.fetch_actionable_issues(["Ready"], terminal_statuses=["Done", "Canceled", "Duplicate"])
    assert [i.identifier for i in result] == ["TEST-1"], result


@pytest.mark.asyncio
async def test_blocker_in_terminal_state_unblocks():
    client = LinearClient(api_key="dummy", project_name="test")
    client._project_id = "proj-1"
    fake_response = {
        "issues": {
            "nodes": [
                {
                    "id": "uuid-B",
                    "identifier": "TEST-2",
                    "title": "B",
                    "priority": 1,
                    "description": "",
                    "assignee": None,
                    "state": {"name": "Ready"},
                    "labels": {"nodes": []},
                    "inverseRelations": {
                        "nodes": [
                            {"type": "blocks", "issue": {"identifier": "TEST-1", "state": {"name": "Done"}}}
                        ]
                    },
                },
            ]
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=fake_response)):
        result = await client.fetch_actionable_issues(["Ready"], terminal_statuses=["Done", "Canceled", "Duplicate"])
    assert [i.identifier for i in result] == ["TEST-2"]


@pytest.mark.asyncio
async def test_self_block_is_warned_and_ignored():
    client = LinearClient(api_key="dummy", project_name="test")
    client._project_id = "proj-1"
    fake_response = {
        "issues": {
            "nodes": [
                {
                    "id": "uuid-A",
                    "identifier": "TEST-1",
                    "title": "A self-block",
                    "priority": 1,
                    "description": "",
                    "assignee": None,
                    "state": {"name": "Ready"},
                    "labels": {"nodes": []},
                    "inverseRelations": {
                        "nodes": [
                            {"type": "blocks", "issue": {"identifier": "TEST-1", "state": {"name": "Ready"}}}
                        ]
                    },
                },
            ]
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=fake_response)):
        result = await client.fetch_actionable_issues(["Ready"], terminal_statuses=["Done"])
    assert [i.identifier for i in result] == ["TEST-1"]
    assert result[0].blocked_by == []


@pytest.mark.asyncio
async def test_terminal_statuses_none_disables_filter():
    client = LinearClient(api_key="dummy", project_name="test")
    client._project_id = "proj-1"
    fake_response = {
        "issues": {
            "nodes": [
                {
                    "id": "uuid-B",
                    "identifier": "TEST-2",
                    "title": "B",
                    "priority": 1,
                    "description": "",
                    "assignee": None,
                    "state": {"name": "Ready"},
                    "labels": {"nodes": []},
                    "inverseRelations": {
                        "nodes": [
                            {"type": "blocks", "issue": {"identifier": "TEST-1", "state": {"name": "Implementing"}}}
                        ]
                    },
                },
            ]
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=fake_response)):
        result = await client.fetch_actionable_issues(["Ready"])
    assert [i.identifier for i in result] == ["TEST-2"]
    assert result[0].blocked_by == [IssueRef(identifier="TEST-1", state="Implementing")]


@pytest.mark.asyncio
async def test_empty_terminal_statuses_disables_filter():
    """Empty list must behave like None — otherwise misconfig deadlocks every blocked issue."""
    client = LinearClient(api_key="dummy", project_name="test")
    client._project_id = "proj-1"
    fake_response = {
        "issues": {
            "nodes": [
                {
                    "id": "uuid-B",
                    "identifier": "TEST-2",
                    "title": "B",
                    "priority": 1,
                    "description": "",
                    "assignee": None,
                    "state": {"name": "Ready"},
                    "labels": {"nodes": []},
                    "inverseRelations": {
                        "nodes": [
                            {"type": "blocks", "issue": {"identifier": "TEST-1", "state": {"name": "Done"}}}
                        ]
                    },
                },
            ]
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=fake_response)):
        result = await client.fetch_actionable_issues(["Ready"], terminal_statuses=[])
    # With empty terminal list, filter must be disabled — issue passes through
    # with its blocker metadata intact, identical to terminal_statuses=None.
    assert [i.identifier for i in result] == ["TEST-2"]
    assert result[0].blocked_by == [IssueRef(identifier="TEST-1", state="Done")]
