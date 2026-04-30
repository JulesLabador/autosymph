"""Tests for ResourcePool namespacing and merge_resources."""

from __future__ import annotations

import pytest

from autosymph.config import ResourcesConfig, SimulatorConfig, RailwayConfig
from autosymph.resources import ResourcePool


class TestNamespacedKeys:
    @pytest.mark.asyncio
    async def test_acquire_with_project_slug(self):
        """Namespaced holder key: {project}:{issue_id}."""
        config = ResourcesConfig(dev_port_range=[3001, 3002])
        pool = ResourcePool(config)
        res = await pool.acquire_for_issue(
            "issue-100", "implement",
            project_slug="demo",
        )
        assert res.holder_id == "demo:issue-100"

    @pytest.mark.asyncio
    async def test_acquire_without_project_slug(self):
        """No project slug — backward compat, bare issue_id."""
        config = ResourcesConfig(dev_port_range=[3001])
        pool = ResourcePool(config)
        res = await pool.acquire_for_issue("issue-100", "implement")
        assert res.holder_id == "issue-100"

    @pytest.mark.asyncio
    async def test_release_all_namespaced(self):
        """release_all with project slug constructs correct key."""
        config = ResourcesConfig(dev_port_range=[3001])
        pool = ResourcePool(config)
        # Acquire with namespace
        await pool.acquire_for_issue(
            "issue-100", "implement",
            project_slug="demo",
        )
        # Release with matching namespace
        pool.release_all("issue-100", project_slug="demo")
        # Pool should have the port back
        assert pool._pools["dev_port"].qsize() == 1


class TestMergeResources:
    def test_dedup_simulators_by_name(self):
        cfg_a = ResourcesConfig(
            ios_simulator=[SimulatorConfig(name="iPhone 17 Pro", udid="auto")],
            dev_port_range=[3001, 3002],
        )
        cfg_b = ResourcesConfig(
            ios_simulator=[
                SimulatorConfig(name="iPhone 17 Pro", udid="auto"),  # duplicate
                SimulatorConfig(name="iPhone 17", udid="auto"),
            ],
            dev_port_range=[3002, 3003],
        )
        merged = ResourcePool.merge_resources([cfg_a, cfg_b])
        assert len(merged.ios_simulator) == 2
        names = {s.name for s in merged.ios_simulator}
        assert names == {"iPhone 17 Pro", "iPhone 17"}

    def test_union_ports(self):
        cfg_a = ResourcesConfig(dev_port_range=[3001, 3002])
        cfg_b = ResourcesConfig(dev_port_range=[3002, 3003, 3004])
        merged = ResourcePool.merge_resources([cfg_a, cfg_b])
        assert merged.dev_port_range == [3001, 3002, 3003, 3004]

    def test_first_railway_wins(self):
        cfg_a = ResourcesConfig(
            railway=RailwayConfig(preview_url_pattern="https://a.example.com")
        )
        cfg_b = ResourcesConfig(
            railway=RailwayConfig(preview_url_pattern="https://b.example.com")
        )
        merged = ResourcePool.merge_resources([cfg_a, cfg_b])
        assert merged.railway is not None
        assert "a.example.com" in merged.railway.preview_url_pattern

    def test_empty_configs(self):
        merged = ResourcePool.merge_resources([ResourcesConfig(), ResourcesConfig()])
        assert merged.ios_simulator == []
        assert merged.dev_port_range == []
        assert merged.railway is None
