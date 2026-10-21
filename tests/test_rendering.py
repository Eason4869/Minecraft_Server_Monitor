import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock


def test_render_pages_escape_input_and_respect_display_options():
    from minecraft_multi_monitor.clients import unavailable_snapshot
    from minecraft_multi_monitor.models import DisplayOptions, ServerConfig
    from minecraft_multi_monitor.rendering import CardRenderer

    async def run():
        server = ServerConfig("s", True, "<script>alert(1)</script>", "private.example", 25565, "java", 45)
        snapshot = replace(
            unavailable_snapshot(server), status="online", motd="<img src=x onerror=alert(1)>", online=5
        )
        render = AsyncMock(return_value="https://renderer/card.png")
        renderer = CardRenderer(render)
        results = await renderer.render_pages([snapshot] * 5, DisplayOptions(show_server_address=False))
        assert len(results) == 2
        assert render.await_count == 2
        first_data = render.call_args_list[0].args[1]
        assert "private.example" not in str(first_data)
        assert "&lt;script&gt;" in str(first_data)
        assert "<script>" not in str(first_data)
        again = await renderer.render_pages([snapshot] * 5, DisplayOptions(show_server_address=False))
        assert again == results
        assert render.await_count == 2

    asyncio.run(run())


def test_invalid_icon_never_becomes_external_resource():
    from minecraft_multi_monitor.clients import unavailable_snapshot
    from minecraft_multi_monitor.models import DisplayOptions, ServerConfig
    from minecraft_multi_monitor.rendering import CardRenderer

    async def run():
        server = ServerConfig("s", True, "Test", "example.com", 25565, "java", 45)
        render = AsyncMock(return_value="https://renderer/card.png")
        await CardRenderer(render).render_pages(
            [replace(unavailable_snapshot(server), icon="https://evil.example/x")], DisplayOptions()
        )
        assert "evil.example" not in str(render.call_args.args[1])

    asyncio.run(run())


def test_quote_is_escaped_in_card():
    from minecraft_multi_monitor.clients import unavailable_snapshot
    from minecraft_multi_monitor.models import DisplayOptions, ServerConfig
    from minecraft_multi_monitor.rendering import CardRenderer

    async def run():
        server = ServerConfig("s", True, "Test", "example.com", 25565, "java", 45)
        render = AsyncMock(return_value="https://renderer/card.png")
        await CardRenderer(render).render_pages(
            [unavailable_snapshot(server)], DisplayOptions(), "<b>quote</b>"
        )
        assert render.call_args.args[1]["quote"] == "&lt;b&gt;quote&lt;/b&gt;"

    asyncio.run(run())


def test_multiline_motd_keeps_continuation_text_in_its_value():
    from minecraft_multi_monitor.clients import unavailable_snapshot
    from minecraft_multi_monitor.models import DisplayOptions, ServerConfig
    from minecraft_multi_monitor.rendering import CardRenderer

    async def run():
        server = ServerConfig("s", True, "Test", "example.com", 25565, "java", 45)
        snapshot = replace(unavailable_snapshot(server), status="online", motd="第一行\n<第二行>")
        render = AsyncMock(return_value="https://renderer/card.png")
        renderer = CardRenderer(render)
        await renderer.render_pages([snapshot], DisplayOptions())
        fields = render.call_args.args[1]["servers"][0]["fields"]
        motd = next(field for field in fields if field["label"] == "MOTD")
        assert motd["value"] == "第一行\n&lt;第二行&gt;"
        await renderer.close()

    asyncio.run(run())
