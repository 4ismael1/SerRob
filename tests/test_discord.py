import asyncio
from dataclasses import replace

import pytest

from serverbot.config import Settings
from serverbot.discord_app import ConfigModal, PanelView, ServerBot, panel_embed
from serverbot.models import Panel


def test_persistent_controls_and_panel_isolation(tmp_path):
    async def scenario():
        bot = ServerBot(Settings(database=str(tmp_path / "discord.db"), max_places=1))
        await bot.store.initialize()
        p = Panel("abcd12345678", "10", "20", "30", "@everyone *game*", message_id="40")
        await bot.store.save(p)
        view = PanelView(bot, p, [])
        assert view.is_persistent()
        assert len({item.custom_id for item in view.children}) == len(view.children)
        assert view.children[0].disabled
        modal = ConfigModal(bot, p)
        assert len(modal.children) == 5
        with pytest.raises(ValueError):
            await bot.change(p.id, 11, max_players=0)
        await bot.change(p.id, 10, max_players=0, interval=5)
        saved = await bot.store.get(p.id)
        assert saved.max_players == 0 and saved.interval == 5
        with pytest.raises(ValueError):
            await bot.change(p.id, 10, expected_version=1, interval=10)
        with pytest.raises(ValueError):
            bot.check_capacity([saved], "another-place")
        # Another panel of the same place consumes no additional scan slot.
        bot.check_capacity([saved], "30")
        embed = panel_embed(saved, bot.engine)
        assert len(embed) < 6000
        assert "@everyone" not in embed.title
        # Command schema can serialize for registration without a token or network.
        for command in bot.tree.get_commands():
            assert command.to_dict(bot.tree)["name"] in {"panel", "estado"}
        await bot.close()
    asyncio.run(scenario())
