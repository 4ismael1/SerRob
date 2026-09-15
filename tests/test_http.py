import asyncio
from contextlib import asynccontextmanager

from aiohttp import web
import pytest

from serverbot.roblox import ProviderError, Roblox


@asynccontextmanager
async def endpoint(handler):
    app = web.Application()
    app.router.add_get("/", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}/"
    finally:
        await runner.cleanup()


def test_actual_http_429_and_age_header():
    async def scenario():
        async def limited(request):
            return web.json_response({}, status=429, headers={"Retry-After": "120"})
        client = Roblox(30)
        await client.start()
        async with endpoint(limited) as url:
            with pytest.raises(ProviderError) as caught:
                await client.get(url)
            assert caught.value.code == "rate_limit"
            assert caught.value.retry_seconds >= 120
            assert client.rate_limits == 1
        await client.close()

        async def cached(request):
            return web.json_response({"data": []}, headers={"Age": "120"})
        client = Roblox(30)
        await client.start()
        async with endpoint(cached) as url:
            import time
            data, observed = await client.get(url)
            assert data == {"data": []}
            assert time.time() - observed >= 120
        await client.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("status,code", [(401, "restricted"), (403, "restricted"), (400, "bad_request"), (404, "not_found"), (503, "upstream"), (302, "http")])
def test_http_errors_are_classified_without_following_redirects(status, code):
    async def scenario():
        async def handler(request):
            return web.Response(status=status, headers={"Location": "https://example.invalid"})
        client = Roblox(30)
        await client.start()
        try:
            async with endpoint(handler) as url:
                with pytest.raises(ProviderError) as caught:
                    await client.get(url)
                assert caught.value.code == code
        finally:
            await client.close()
    asyncio.run(scenario())
