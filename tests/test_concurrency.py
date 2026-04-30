"""Tests for ConcurrencyManager — fair-share, atomicity, edge cases."""

from __future__ import annotations

import asyncio

import pytest

from autosymph.concurrency import ConcurrencyManager


@pytest.fixture
def cm() -> ConcurrencyManager:
    """ConcurrencyManager with global_max=5, 3 projects registered."""
    mgr = ConcurrencyManager(global_max=5)
    mgr.register_project("alpha", max_agents=3)
    mgr.register_project("beta", max_agents=3)
    mgr.register_project("gamma", max_agents=3)
    return mgr


class TestFairShareBasic:
    @pytest.mark.asyncio
    async def test_each_project_gets_one(self, cm: ConcurrencyManager):
        """Each project can acquire at least 1 slot."""
        assert await cm.acquire("alpha")
        assert await cm.acquire("beta")
        assert await cm.acquire("gamma")

    @pytest.mark.asyncio
    async def test_surplus_distributed(self, cm: ConcurrencyManager):
        """After each gets 1, surplus goes to whoever asks."""
        for slug in ("alpha", "beta", "gamma"):
            assert await cm.acquire(slug)
        # 2 surplus slots — alpha and beta each get 1 more
        assert await cm.acquire("alpha")
        assert await cm.acquire("beta")
        # Now at global cap
        assert not await cm.acquire("gamma")

    @pytest.mark.asyncio
    async def test_global_cap_enforced(self, cm: ConcurrencyManager):
        """Cannot exceed global_max total."""
        for slug in ("alpha", "beta", "gamma"):
            assert await cm.acquire(slug)
        assert await cm.acquire("alpha")
        assert await cm.acquire("beta")
        # At 5/5 — all denied
        assert not await cm.acquire("alpha")
        assert not await cm.acquire("beta")
        assert not await cm.acquire("gamma")


class TestPerProjectLimit:
    @pytest.mark.asyncio
    async def test_per_project_max_respected(self):
        mgr = ConcurrencyManager(global_max=10)
        mgr.register_project("small", max_agents=2)
        assert await mgr.acquire("small")
        assert await mgr.acquire("small")
        assert not await mgr.acquire("small")  # per-project limit 2

    @pytest.mark.asyncio
    async def test_per_project_independent_of_global(self):
        mgr = ConcurrencyManager(global_max=10)
        mgr.register_project("tiny", max_agents=1)
        mgr.register_project("big", max_agents=5)
        assert await mgr.acquire("tiny")
        assert not await mgr.acquire("tiny")  # limited to 1
        # But big can still acquire
        for _ in range(5):
            assert await mgr.acquire("big")


class TestMinimumGuarantee:
    @pytest.mark.asyncio
    async def test_minimum_1_per_project(self):
        """3 global slots, 3 projects — each gets exactly 1."""
        mgr = ConcurrencyManager(global_max=3)
        mgr.register_project("a", max_agents=3)
        mgr.register_project("b", max_agents=3)
        mgr.register_project("c", max_agents=3)
        assert await mgr.acquire("a")
        assert await mgr.acquire("b")
        assert await mgr.acquire("c")
        # All at cap — none can get more
        assert not await mgr.acquire("a")

    @pytest.mark.asyncio
    async def test_fair_share_reserves_for_others(self):
        """5 slots, 3 projects. Alpha can take surplus but not starve others."""
        mgr = ConcurrencyManager(global_max=5)
        mgr.register_project("alpha", max_agents=5)
        mgr.register_project("beta", max_agents=5)
        mgr.register_project("gamma", max_agents=5)

        # Alpha gets 1
        assert await mgr.acquire("alpha")
        # Alpha gets 2nd — remaining_after=2, projects_needing=2, 2 > 2 false → granted
        assert await mgr.acquire("alpha")
        # Alpha gets 3rd — remaining_after=1, projects_needing=2, 2 > 1 → denied
        assert await mgr.acquire("alpha")
        # Alpha tries 4th — remaining_after=0, projects_needing=2, 2 > 0 → denied
        assert not await mgr.acquire("alpha")
        # Beta and gamma can still get theirs
        assert await mgr.acquire("beta")
        assert await mgr.acquire("gamma")


class TestRelease:
    @pytest.mark.asyncio
    async def test_release_frees_slot(self, cm: ConcurrencyManager):
        """Releasing a slot allows another acquire."""
        for slug in ("alpha", "beta", "gamma"):
            assert await cm.acquire(slug)
        assert await cm.acquire("alpha")
        assert await cm.acquire("beta")
        # At cap
        assert not await cm.acquire("gamma")
        # Release one from alpha
        await cm.release("alpha")
        # Now gamma can acquire
        assert await cm.acquire("gamma")

    @pytest.mark.asyncio
    async def test_release_unregistered(self, cm: ConcurrencyManager):
        """Releasing unregistered project is a no-op (logged)."""
        await cm.release("nonexistent")  # Should not raise


class TestProjectsExceedGlobalCap:
    @pytest.mark.asyncio
    async def test_more_projects_than_slots(self):
        """4 projects, 2 global slots. First 2 get slots, others wait."""
        mgr = ConcurrencyManager(global_max=2)
        mgr.register_project("a", max_agents=3)
        mgr.register_project("b", max_agents=3)
        mgr.register_project("c", max_agents=3)
        mgr.register_project("d", max_agents=3)

        assert await mgr.acquire("a")
        assert await mgr.acquire("b")
        # At cap — c and d are denied
        assert not await mgr.acquire("c")
        assert not await mgr.acquire("d")
        # Release a → c can now acquire
        await mgr.release("a")
        assert await mgr.acquire("c")


class TestLateRunnableProject:
    @pytest.mark.asyncio
    async def test_late_project_competes_on_release(self):
        """Project C becomes runnable after A/B hold surplus — best-effort."""
        mgr = ConcurrencyManager(global_max=4)
        mgr.register_project("a", max_agents=3)
        mgr.register_project("b", max_agents=3)
        mgr.register_project("c", max_agents=3)

        # A and B each get 2 (filling up)
        assert await mgr.acquire("a")
        assert await mgr.acquire("b")
        assert await mgr.acquire("a")
        # Now 3/4 held. C tries — should get 1 (fair-share reserves it)
        assert await mgr.acquire("c")
        # At 4/4 cap. Release one from A
        await mgr.release("a")
        # C can now compete for the freed slot
        assert await mgr.acquire("c")


class TestAtomicAcquire:
    @pytest.mark.asyncio
    async def test_concurrent_acquires_respect_cap(self):
        """10 concurrent coroutines racing to acquire — never exceeds global cap."""
        mgr = ConcurrencyManager(global_max=5)
        mgr.register_project("race", max_agents=10)

        results = await asyncio.gather(
            *[mgr.acquire("race") for _ in range(10)]
        )
        granted = sum(1 for r in results if r)
        assert granted == 5
        assert mgr.status()["global_held"] == 5


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_snapshot(self, cm: ConcurrencyManager):
        await cm.acquire("alpha")
        await cm.acquire("beta")
        s = cm.status()
        assert s["global_max"] == 5
        assert s["global_held"] == 2
        assert s["projects"]["alpha"]["held"] == 1
        assert s["projects"]["beta"]["held"] == 1
        assert s["projects"]["gamma"]["held"] == 0


class TestUnregistered:
    @pytest.mark.asyncio
    async def test_acquire_unregistered(self):
        mgr = ConcurrencyManager(global_max=5)
        assert not await mgr.acquire("ghost")

    @pytest.mark.asyncio
    async def test_duplicate_register(self):
        mgr = ConcurrencyManager(global_max=5)
        mgr.register_project("x", max_agents=3)
        mgr.register_project("x", max_agents=5)  # Should warn, not replace
        assert mgr._projects["x"].max_agents == 3  # Original preserved
