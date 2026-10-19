"""Read-only production integration check. Requires the service environment."""

import asyncio

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from damavand_bot.config import Config
from damavand_bot.xui import XUIClient


async def main() -> None:
    config = Config.from_env()
    bot = Bot(config.bot_token)
    try:
        me = await bot.get_me()
        channel_ref = int(config.channel_id) if config.channel_id else config.channel_username
        channel = await bot.get_chat(channel_ref)
        try:
            admin_member = await bot.get_chat_member(channel_ref, next(iter(config.admin_ids)))
            admin_status = admin_member.status
        except TelegramBadRequest as exc:
            admin_status = f"unavailable ({exc.message})"
        print(
            "telegram",
            me.username,
            "channel",
            channel.title,
            "admin_member",
            admin_status,
        )
    finally:
        await bot.session.close()

    async with XUIClient(config) as xui:
        healthy = await xui.health()
        inbounds = await xui.enabled_inbound_ids()
        print("xui", healthy, "inbounds", inbounds)


if __name__ == "__main__":
    asyncio.run(main())
