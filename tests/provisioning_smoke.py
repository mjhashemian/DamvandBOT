"""Create, verify and always delete a tiny 3x-ui smoke-test client."""

import asyncio
import time

from damavand_bot.config import Config
from damavand_bot.xui import XUIClient


async def main() -> None:
    config = Config.from_env()
    email = f"dvbot-smoke-{int(time.time())}"
    async with XUIClient(config) as xui:
        created = False
        try:
            envelope = await xui.create_client(
                email=email,
                telegram_id=306525404,
                traffic_bytes=1024 * 1024,
                duration_days=1,
                comment="Damavand bot provisioning smoke test - delete immediately",
            )
            created = True
            inbound_ids = sorted(int(value) for value in envelope.get("inboundIds", []))
            links = await xui.links(email)
            subscription = xui.subscription_url(envelope)
            assert inbound_ids == sorted(await xui.enabled_inbound_ids()), inbound_ids
            assert len(links) == len(inbound_ids), (len(links), inbound_ids)
            assert subscription.startswith("https://"), subscription
            print("provisioning ok", "inbounds", inbound_ids, "links", len(links))
        finally:
            if created:
                await xui.delete_client(email)
                print("cleanup ok")


if __name__ == "__main__":
    asyncio.run(main())
