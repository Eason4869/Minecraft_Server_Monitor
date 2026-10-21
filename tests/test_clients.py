import asyncio
from dataclasses import replace

from aiohttp import web

from minecraft_multi_monitor.clients import MCStatusClient, MineBBSClient, build_status_client
from minecraft_multi_monitor.models import ServerConfig


def server(kind="java"):
    return ServerConfig("s", True, "Test", "example.com", 25565, kind, 45)


def test_sessions_reused_and_closed_with_real_http():
    async def run():
        hits = []

        async def handler(request):
            hits.append(request.path)
            return web.json_response({"online": True, "version": {"name_clean": "1.21"}})

        app = web.Application()
        app.router.add_get("/{tail:.*}", handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        client = MCStatusClient()
        client.BASE_URL = f"http://127.0.0.1:{port}"
        try:
            assert (await client.fetch_status(server(), "test")).version == "1.21"
            session = client.session
            await client.fetch_status(server(), "test")
            assert client.session is session
            assert len(hits) == 2
        finally:
            await client.close()
            await runner.cleanup()
        assert session.closed

    asyncio.run(run())


def test_minebbs_does_not_double_query_online_server():
    async def run():
        client = MineBBSClient()
        count = 0

        async def fetch(*args):
            nonlocal count
            count += 1
            return client._parse_response(server(), {"status": "online", "players": {"online": 2}})

        client._fetch_with_stype = fetch
        assert (await client.fetch_status(server(), "test")).online == 2
        assert count == 1

    asyncio.run(run())


def test_failover_only_on_query_failure():
    async def run():
        client = build_status_client(True)
        hits = []

        async def fail(*args):
            return None

        async def backup(*args):
            hits.append(1)
            return client.secondary._parse_response(server(), {"online": False})

        client.primary.fetch_status = fail
        client.secondary.fetch_status = backup
        assert (await client.fetch_status(server(), "test")).status == "offline"
        assert hits == [1]
        client.primary.fetch_status = backup
        await client.fetch_status(server(), "test")
        assert hits == [1, 1]
        await client.close()

    asyncio.run(run())


def test_api_timestamp_is_preserved():
    result = MCStatusClient()._parse_response(server(), {"online": True, "retrieved_at": 0})
    assert "1970" in result.update_time
    assert result.source == "mcstatus.io"


def test_bedrock_version_and_empty_names():
    result = MCStatusClient()._parse_response(
        server("bedrock"),
        {"online": True, "version": {"name": "1.21.4"}, "players": {"online": 5, "max": 10}},
    )
    assert result.version == "1.21.4"
    assert result.players == []
    assert replace(result, status="unknown").status == "unknown"
