import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ModelRouter, RateLimitExceeded, SQLiteTokenRateLimiter, estimate_tokens


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.limiter = SQLiteTokenRateLimiter(Path(self.temp.name) / "limits.db", limit=100, window_seconds=60)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_limit_and_eviction(self):
        await self.limiter.reserve("tenant", 60, now_ms=1_000_000)
        with self.assertRaises(RateLimitExceeded):
            await self.limiter.reserve("tenant", 41, now_ms=1_000_001)
        await self.limiter.reserve("tenant", 100, now_ms=1_060_001)
        self.assertEqual(await self.limiter.usage("tenant", now_ms=1_060_001), 100)

    async def test_tenants_are_isolated(self):
        await self.limiter.reserve("one", 100, now_ms=1_000_000)
        await self.limiter.reserve("two", 100, now_ms=1_000_000)
        self.assertEqual(await self.limiter.usage("two", now_ms=1_000_000), 100)

    async def test_release_removes_reservation(self):
        reservation = await self.limiter.reserve("tenant", 50, now_ms=1_000_000)
        await self.limiter.release(reservation)
        self.assertEqual(await self.limiter.usage("tenant", now_ms=1_000_000), 0)

    async def test_concurrent_reservations_are_atomic(self):
        outcomes = await asyncio.gather(
            self.limiter.reserve("tenant", 60, now_ms=1_000_000),
            self.limiter.reserve("tenant", 60, now_ms=1_000_000),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(item, str) for item in outcomes), 1, repr(outcomes))
        self.assertEqual(
            sum(isinstance(item, RateLimitExceeded) for item in outcomes),
            1,
            repr(outcomes),
        )


class RouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_success(self):
        def handler(request):
            return httpx.Response(200, json={"id": "primary"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await ModelRouter("https://primary", "https://backup", client=client).complete({})
        self.assertEqual(result.provider, "primary")

    async def test_429_falls_back(self):
        def handler(request):
            if request.url.host == "primary":
                return httpx.Response(429, json={"error": "limited"})
            return httpx.Response(200, json={"id": "backup"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await ModelRouter("https://primary", "https://backup", client=client).complete({})
        self.assertEqual(result.provider, "secondary")
        self.assertEqual(result.payload["id"], "backup")

    async def test_wall_clock_timeout_falls_back(self):
        async def handler(request):
            if request.url.host == "primary":
                await asyncio.sleep(0.05)
                return httpx.Response(200, json={"id": "too-late"})
            return httpx.Response(200, json={"id": "backup"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await ModelRouter(
                "https://primary",
                "https://backup",
                primary_timeout=0.01,
                client=client,
            ).complete({})
        self.assertEqual(result.provider, "secondary")


class EstimateTests(unittest.TestCase):
    def test_estimate_includes_output_budget(self):
        body = {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 25}
        self.assertGreaterEqual(estimate_tokens(body), 26)

    def test_rejects_fractional_max_tokens(self):
        with self.assertRaises(ValueError):
            estimate_tokens({"prompt": "hello", "max_tokens": 2.5})


if __name__ == "__main__":
    unittest.main()

