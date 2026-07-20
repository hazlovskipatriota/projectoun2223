import os
import re
import io
import datetime
import random
import discord
import aiohttp
import asyncio
from aiohttp import web
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from gemini import generateResponseGemini, generateImageImagen

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
LOG_CHANNEL_ID = 1528172889143119872

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True  # Śledzenie zmian stanów głosowych

client = discord.Client(intents=intents)

user_dm_state = {}
SONGS_DIR = "songs"


def split_message(text, limit=2000):
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


@client.event
async def on_voice_state_update(member, before, after):
    # 1. Reakcja na wchodzenie ludzi na kanał
    if member.id != client.user.id:
        if after.channel and before.channel != after.channel:
            voice_channel = after.channel
            guild = voice_channel.guild
            voice_client = discord.utils.get(client.voice_clients, guild=guild)

            if not voice_client:
                try:
                    voice_client = await voice_channel.connect(timeout=10.0, reconnect=True)
                    print(f"[Voice] Połączono z kanałem: {voice_channel.name}")
                except Exception as e:
                    print(f"[Voice BŁĄD] Nie można połączyć z kanałem: {e}")
                    return

            if voice_client and not voice_client.is_playing():
                await play_song(voice_client)

    # 2. Reakcja na opuszczanie kanału przez ludzi (samotność bota)
    # Pobieramy klienta głosowego dla serwera, na którym nastąpiła zmiana strefy audio
    guild = member.guild
    voice_client = discord.utils.get(client.voice_clients, guild=guild)
    
    if voice_client and voice_client.channel:
        # Liczba użytkowników na kanale (odliczając boty)
        real_users = [m for m in voice_client.channel.members if not m.bot]
        if len(real_users) == 0:
            print(f"[Voice] Zostałem sam na kanale {voice_client.channel.name}. Rozłączam się.")
            await voice_client.disconnect()


async def play_song(voice_client, specific_song=None):
    if not os.path.exists(SONGS_DIR):
        print(f"[Voice BŁĄD] Katalog '{SONGS_DIR}' nie istnieje!")
        return

    songs = [f for f in os.listdir(SONGS_DIR) if f.endswith(('.mp3', '.wav', '.ogg', '.m4a'))]
    
    if not songs:
        print(f"[Voice] Brak plików muzycznych v folderze '{SONGS_DIR}'.")
        return

    # Wybór utworu: konkretny żądany lub losowy
    if specific_song and specific_song in songs:
        chosen_song = specific_song
    else:
        chosen_song = random.choice(songs)

    song_path = os.path.join(SONGS_DIR, chosen_song)
    print(f"[Voice] Odtwarzam plik: {chosen_song}")
    
    # Zatrzymujemy bieżące odtwarzanie, jeśli bot już coś puszczał (przydatne przy skip/wymuszeniu piosenki)
    if voice_client.is_playing():
        voice_client.stop()

    def after_playing(error):
        if error:
            print(f"[Voice BŁĄD] Wyjątek strumienia audio: {error}")
        
        # Jeśli bot po zakończeniu piosenki nadal nie jest sam, gra dalej
        if voice_client.channel and len([m for m in voice_client.channel.members if not m.bot]) > 0:
            coro = play_song(voice_client)
            fut = asyncio.run_coroutine_threadsafe(coro, client.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"[Voice BŁĄD] Błąd pętli muzycznej: {e}")
        else:
            coro = voice_client.disconnect()
            asyncio.run_coroutine_threadsafe(coro, client.loop)
            print("[Voice] Brak słuchaczy. Rozłączanie po utworze.")

    try:
        source = discord.FFmpegPCMAudio(song_path, options="-vn")
        voice_client.play(source, after=after_playing)
    except Exception as e:
        print(f"[Voice BŁĄD] Błąd krytyczny odtwarzacza: {e}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_target_channel = message.channel.id == CHANNEL_ID
    is_mentioned = client.user.mentioned_in(message)

    if not (is_target_channel or is_dm or is_mentioned):
        return

    content_lower = message.content.lower().strip()
    voice_client = discord.utils.get(client.voice_clients, guild=message.guild)

    # === MECHANIZM STEROWANIA MUZYKĄ PRZEZ TEKST ===
    
    # 1. Obsługa żądania pominięcia utworu (Skip)
    if any(cmd in content_lower for cmd in ["skip", "następna", "nastepna", "przełącz piosenkę", "przelacz piosenke"]):
        if voice_client and voice_client.is_playing():
            await message.reply("Odrzucam ten syf, gramy dalej.")
            voice_client.stop() # Wywołanie .stop() automatycznie odpala after_playing, który włączy kolejny losowy kawałek
            return
        else:
            await message.reply("Nic teraz nie leci na kanale głosowym.")
            return

    # 2. Obsługa żądania włączenia konkretnego utworu
    if "puść" in content_lower or "puść utwór" in content_lower or "zagraj" in content_lower:
        if not voice_client:
            await message.reply("Muszę być na kanale głosowym, żeby coś zagrać. Wejdź na kanał!")
            return

        if os.path.exists(SONGS_DIR):
            songs = [f for f in os.listdir(SONGS_DIR) if f.endswith(('.mp3', '.wav', '.ogg', '.m4a'))]
            matched_song = None
            
            # Szukanie dopasowania w nazwach plików
            for song in songs:
                # Sprawdzamy pełną nazwę pliku lub nazwę bez rozszerzenia
                song_name_clean = os.path.splitext(song)[0].lower()
                if song.lower() in content_lower or song_name_clean in content_lower:
                    matched_song = song
                    break
            
            if matched_song:
                await message.reply(f"Włączam zamówiony utwór: {matched_song}")
                await play_song(voice_client, specific_song=matched_song)
                return
            else:
                await message.reply("Nie znalazłem takiej piosenki w moim spisie plików.")
                return

    # === STANDARDOWA OBSŁUGA CZATU GEMINI ===
    async with message.channel.typing():
        try:
            links_context = await extract_and_fetch_links(message.content)
            full_prompt = message.content
            if links_context:
                full_prompt += links_context

            images = []
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    img_part = await download_image_as_part(attachment.url, attachment.content_type)
                    if img_part:
                        images.append(img_part)

            if images:
                raw_response = generateResponseGemini(prompt=full_prompt, image_parts=images)
            else:
                raw_response = generateResponseGemini(prompt=full_prompt)

            if not raw_response:
                await message.reply("Przepraszam, nie udało mi się wygenerować odpowiedzi.")
                return

            chunks = split_message(raw_response)
            for chunk in chunks:
                await message.reply(chunk)

        except Exception as e:
            print(f"[Błąd on_message]: {e}")
            await message.reply("Wystąpił nieoczekiwany błąd podczas przetwarzania wiadomości.")

client.run(TOKEN)