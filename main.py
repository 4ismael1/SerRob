import argparse
import asyncio
import logging
import os
import signal
import sys

from dotenv import load_dotenv

from serverbot.config import Settings
from serverbot.models import parse_place
from serverbot.roblox import ProviderError, Roblox


async def diagnose(settings: Settings, value: str) -> int:
    place = parse_place(value)
    client = Roblox(settings.rpm)
    await client.start()
    try:
        observations, cursor = await client.servers(place)
        print(f"ROBLOX_OK | Place {place} | {len(observations)} servidores válidos | más páginas: {bool(cursor)}")
        counts = sorted(o.playing for o in observations)
        print(f"Ocupaciones observadas (hasta 10): {counts[:10]}")
        print("Esta prueba no garantiza cobertura completa ni entrada a una instancia.")
        return 0
    except ProviderError as exc:
        print(f"ROBLOX_UNAVAILABLE | {exc.code} | {exc}")
        return 2
    finally:
        await client.close()


async def run_bot(settings: Settings):
    from serverbot.discord_app import ServerBot
    async with ServerBot(settings) as bot:
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, lambda: asyncio.create_task(bot.close()))
            except NotImplementedError:
                pass  # Windows: asyncio.run handles Ctrl+C.
        await bot.start(settings.token)


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Paneles de servidores públicos de Roblox para Discord")
    parser.add_argument("--diagnose", metavar="PLACE_ID", help="Prueba una página de Roblox sin token de Discord")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        settings = Settings.from_env()
        if args.diagnose:
            return asyncio.run(diagnose(settings, args.diagnose))
        if not settings.token:
            print("Falta DISCORD_TOKEN. Configúralo en el entorno de Pterodactyl o en .env. Nunca lo publiques en Discord.")
            return 1
        asyncio.run(run_bot(settings))
        return 0
    except KeyboardInterrupt:
        return 0
    except ValueError as exc:
        print(f"Configuración inválida: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
