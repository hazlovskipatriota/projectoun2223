import os
import re
import io
import datetime
import random
import discord
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from discord.ext import tasks

from gemini import generateResponseGemini, generateImageImagen

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
LOG_CHANNEL_ID = 1528172889143119872

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

user_dm_state = {}


def split_message(text, limit=2000):
    """Dzieli tekst na części o maksymalnej długości `limit`."""
    return [text[i:i+limit] for i in range(0, len(text), limit) if text[i:i+limit].strip()]

async def download_image_as_part(url: str, mime_type: str):
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    from google.genai import types
                    data = await resp.read()
                    return types.Part.from_bytes(data=data, mime_type=mime_type)
    except Exception as e:
        print(f"[Obrazy BŁĄD] Nie udało się pobrać obrazu z załącznika: {e}")
    return None


async def extract_and_fetch_links(text: str) -> str:
    urls = re.findall(r'(https?://[^\s]+)', text)
    if not urls:
        return ""

    links_context = "\n[ZAWARTOŚĆ LINKÓW WYSŁANYCH PRZEZ UŻYTKOWNIKA]\n"
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        html = await resp.text()
                        soup = BeautifulSoup(html, "html.parser")
                        for script in soup(["script", "style"]):
                            script.decompose()
                        title = soup.title.string.strip() if soup.title else "Brak tytułu strony"
                        page_text = " ".join(soup.get_text().split())[:1500]
                        links_context += f"Link: {url}\nTytuł strony: {title}\nTreść strony (skrót): {page_text}\n---\n"
            except Exception as e:
                links_context += f"[Linki BŁĄD] Link: {url} (Błąd pobierania: {e})\n"
    return links_context


async def fetch_reply_chain(message: discord.Message, channel, max_depth=10):
    chain = []
    current_msg = message
    depth = 0
    try:
        while current_msg.reference and current_msg.reference.message_id and depth < max_depth:
            ref_id = current_msg.reference.message_id
            # Zabezpieczenie przed zapętleniem na tej samej wiadomości
            if any(m.id == ref_id for m in chain) or current_msg.id == ref_id:
                break
            parent_msg = await channel.fetch_message(ref_id)
            chain.append(parent_msg)
            current_msg = parent_msg
            depth += 1
    except discord.Forbidden:
        print("[Wątek] Brak uprawnień do pobierania historii wątków wiadomości.")
    except Exception as e:
        print(f"[Wątek BŁĄD] Przerwano pobieranie łańcucha na głębokości {depth}: {e}")
    chain.reverse()
    return chain

@client.event
async def on_ready():
    print(f"=========================================")
    print(f"Zalogowano pomyślnie jako: {client.user}")
    print(f"=========================================")

    if not hasattr(client, "web_started"):
        client.web_started = True
        await start_webserver()

async def healthcheck(request):
    return web.Response(text="OK")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", healthcheck)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[Serwer HTTP] Serwer nasłuchuje na porcie: {port}")

client.run(TOKEN)
