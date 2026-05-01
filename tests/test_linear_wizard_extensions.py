"""Tests for the LinearClient methods added for the onboarding wizard.

Covers list_projects, create_workflow_state, create_label, and the
LINEAR_API_URL env override (per PRD R22 + task 2.6).
"""
import pytest
from unittest.mock import AsyncMock, patch

from autosymph.linear_client import LinearClient


@pytest.mark.asyncio
async def test_list_projects_returns_paginated_results():
    """list_projects walks pages and returns flattened project dicts."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    page1 = {
        "projects": {
            "nodes": [
                {
                    "id": "p1",
                    "name": "Alpha",
                    "slugId": "alpha-slug",
                    "teams": {"nodes": [{"id": "t1", "name": "Team Alpha"}]},
                },
            ],
            "pageInfo": {"hasNextPage": True, "endCursor": "cur1"},
        }
    }
    page2 = {
        "projects": {
            "nodes": [
                {
                    "id": "p2",
                    "name": "Beta",
                    "slugId": "beta-slug",
                    "teams": {"nodes": []},
                },
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    }
    with patch.object(client, "_query", new=AsyncMock(side_effect=[page1, page2])):
        result = await client.list_projects()

    assert len(result) == 2
    assert result[0] == {
        "id": "p1",
        "name": "Alpha",
        "slug_id": "alpha-slug",
        "team_ids": ["t1"],
        "team_names": ["Team Alpha"],
    }
    assert result[1]["name"] == "Beta"
    assert result[1]["team_ids"] == []


@pytest.mark.asyncio
async def test_list_projects_single_page():
    """list_projects exits the loop when hasNextPage is False on the first page."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    only_page = {
        "projects": {
            "nodes": [
                {
                    "id": "p1",
                    "name": "Solo",
                    "slugId": "solo",
                    "teams": {"nodes": [{"id": "t1", "name": "Solo Team"}]},
                },
            ],
            "pageInfo": {"hasNextPage": False, "endCursor": None},
        }
    }
    mock = AsyncMock(return_value=only_page)
    with patch.object(client, "_query", new=mock):
        result = await client.list_projects()
    assert len(result) == 1
    assert mock.call_count == 1


@pytest.mark.asyncio
async def test_create_workflow_state_returns_id():
    """create_workflow_state extracts the new state's id on success."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {
        "workflowStateCreate": {
            "success": True,
            "workflowState": {"id": "state-123", "name": "Implementing"},
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=response)) as mock:
        state_id = await client.create_workflow_state(
            team_id="team-1",
            name="Implementing",
            color="#ff0000",
            position=2.0,
        )

    assert state_id == "state-123"
    # Verify the mutation was called with the expected variables
    _, kwargs_or_args = mock.call_args
    variables = mock.call_args[0][1]  # second positional arg
    assert variables["input"]["teamId"] == "team-1"
    assert variables["input"]["name"] == "Implementing"
    assert variables["input"]["color"] == "#ff0000"
    assert variables["input"]["position"] == 2.0
    assert variables["input"]["type"] == "started"  # default


@pytest.mark.asyncio
async def test_create_workflow_state_passes_state_type():
    """create_workflow_state respects the state_type override (e.g. 'completed' for terminals)."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {
        "workflowStateCreate": {
            "success": True,
            "workflowState": {"id": "done-id", "name": "Done"},
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=response)) as mock:
        await client.create_workflow_state(
            team_id="team-1",
            name="Done",
            color="#00ff00",
            position=99.0,
            state_type="completed",
        )
    variables = mock.call_args[0][1]
    assert variables["input"]["type"] == "completed"


@pytest.mark.asyncio
async def test_create_workflow_state_raises_on_failure():
    """create_workflow_state raises RuntimeError when success is False."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {"workflowStateCreate": {"success": False, "workflowState": None}}
    with patch.object(client, "_query", new=AsyncMock(return_value=response)):
        with pytest.raises(RuntimeError, match="workflowStateCreate failed"):
            await client.create_workflow_state(
                team_id="team-1",
                name="X",
                color="#111",
                position=1.0,
            )


@pytest.mark.asyncio
async def test_create_workflow_state_raises_when_no_id():
    """create_workflow_state raises if success=True but no id returned (defensive)."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {"workflowStateCreate": {"success": True, "workflowState": None}}
    with patch.object(client, "_query", new=AsyncMock(return_value=response)):
        with pytest.raises(RuntimeError, match="returned no id"):
            await client.create_workflow_state(
                team_id="team-1", name="X", color="#111", position=1.0,
            )


@pytest.mark.asyncio
async def test_create_label_workspace_scoped():
    """create_label without team_id sends a workspace-scoped input (no teamId)."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {
        "issueLabelCreate": {
            "success": True,
            "issueLabel": {"id": "lab-1", "name": "needs-review"},
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=response)) as mock:
        label_id = await client.create_label(name="needs-review", color="#eb5757")
    assert label_id == "lab-1"
    variables = mock.call_args[0][1]
    assert variables["input"]["name"] == "needs-review"
    assert variables["input"]["color"] == "#eb5757"
    assert "teamId" not in variables["input"]


@pytest.mark.asyncio
async def test_create_label_team_scoped():
    """create_label with team_id sends teamId in the input."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {
        "issueLabelCreate": {
            "success": True,
            "issueLabel": {"id": "lab-2", "name": "team-only"},
        }
    }
    with patch.object(client, "_query", new=AsyncMock(return_value=response)) as mock:
        await client.create_label(name="team-only", color="#000", team_id="team-99")
    variables = mock.call_args[0][1]
    assert variables["input"]["teamId"] == "team-99"


@pytest.mark.asyncio
async def test_create_label_raises_on_failure():
    """create_label raises when success is False."""
    client = LinearClient(api_key="dummy", project_name="ignored")
    response = {"issueLabelCreate": {"success": False, "issueLabel": None}}
    with patch.object(client, "_query", new=AsyncMock(return_value=response)):
        with pytest.raises(RuntimeError, match="issueLabelCreate failed"):
            await client.create_label(name="x", color="#111")


def test_linear_api_url_override(monkeypatch):
    """LINEAR_API_URL env var overrides the default endpoint per PRD R22."""
    monkeypatch.setenv("LINEAR_API_URL", "http://localhost:9999/graphql")
    client = LinearClient(api_key="dummy", project_name="ignored")
    assert client.api_url == "http://localhost:9999/graphql"


def test_linear_api_url_default_unchanged(monkeypatch):
    """When LINEAR_API_URL is unset, api_url uses the public Linear endpoint."""
    monkeypatch.delenv("LINEAR_API_URL", raising=False)
    client = LinearClient(api_key="dummy", project_name="ignored")
    assert client.api_url == LinearClient.API_URL
    assert client.api_url == "https://api.linear.app/graphql"
