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
from discord.ext import tasks

from gemini import generateResponseGemini, generateImageImagen

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
LOG_CHANNEL_ID = 1528172889143119872

FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")

# Konfiguracja uprawnień bota
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True  # Wymagane do poprawnego śledzenia kanałów głosowych

client = discord.Client(intents=intents)

user_dm_state = {}
SONGS_DIR = "songs"  # Nazwa folderu z piosenkami muzycznymi


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

<<<<<<< HEAD

@client.event
async def on_voice_state_update(member, before, after):
    # Ignoruj aktywność samego bota
    if member.id == client.user.id:
        return

    # Jeśli użytkownik wszedł na kanał głosowy (i nie był na nim wcześniej)
    if after.channel and before.channel != after.channel:
        voice_channel = after.channel
        guild = voice_channel.guild

        # Sprawdź, czy bot jest już połączony w obrębie tego serwera
        voice_client = discord.utils.get(client.voice_clients, guild=guild)

        if not voice_client:
            try:
                voice_client = await voice_channel.connect()
            except Exception as e:
                print(f"[Voice BŁĄD] Nie można połączyć z kanałem: {e}")
                return

        # Jeśli bot połączył się pomyślnie i w tym momencie nic nie gra
        if voice_client and not voice_client.is_playing():
            await play_random_song(voice_client)


async def play_random_song(voice_client):
    if not os.path.exists(SONGS_DIR):
        print(f"[Voice BŁĄD] Katalog '{SONGS_DIR}' nie istnieje!")
        return

    # Pobieranie listy dostępnych plików muzycznych
    songs = [f for f in os.listdir(SONGS_DIR) if f.endswith(('.mp3', '.wav', '.ogg', '.m4a'))]
    
    if not songs:
        print(f"[Voice] Brak utworów muzycznych w katalogu '{SONGS_DIR}'.")
        return

    random_song = random.choice(songs)
    song_path = os.path.join(SONGS_DIR, random_song)

    print(f"[Voice] Odtwarzam losowy utwór: {random_song}")
    
    def after_playing(error):
        if error:
            print(f"[Voice BŁĄD] Wystąpił błąd podczas odtwarzania: {error}")
        
        # Sprawdzanie, czy na kanale wciąż znajduje się ktoś oprócz bota
        if voice_client.channel and len(voice_client.channel.members) > 1:
            # Wywołanie kolejnej losowej piosenki w bezpieczny sposób
            coro = play_random_song(voice_client)
            fut = asyncio.run_coroutine_threadsafe(coro, client.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"[Voice BŁĄD] Nie udało się wywołać kolejnego utworu: {e}")
        else:
            # Jeśli nikogo już nie ma, bot opuszcza kanał
            coro = voice_client.disconnect()
            asyncio.run_coroutine_threadsafe(coro, client.loop)
            print("[Voice] Kanał jest pusty. Bot rozłączył się.")

    try:
        # Uruchomienie odtwarzania dźwięku przez FFmpeg
        voice_client.play(discord.FFmpegPCMAudio(song_path), after=after_playing)
    except Exception as e:
        print(f"[Voice BŁĄD] Wyjątek podczas próby odtworzenia {random_song}: {e}")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

=======
@client.event
async def on_message(message: discord.Message):
    # 1. Ignoruj wiadomości wysyłane przez samego bota, żeby uniknąć pętli
    if message.author == client.user:
        return

    # 2. Reaguj tylko w wybranym kanale (CHANNEL_ID) LUB na wiadomości prywatne (DM) LUB gdy bot zostanie oznaczony (@Bot)
>>>>>>> cfa1fb384365aa25a885bf6327d102d7924bf8d5
    is_dm = isinstance(message.channel, discord.DMChannel)
    is_target_channel = message.channel.id == CHANNEL_ID
    is_mentioned = client.user.mentioned_in(message)

    if not (is_target_channel or is_dm or is_mentioned):
        return

<<<<<<< HEAD
    async with message.channel.typing():
        try:
=======
    # Uruchomienie wskaźnika pisania ("Bot pisze...")
    async with message.channel.typing():
        try:
            # 3. Zbieranie dodatkowego kontekstu (linki z treści)
>>>>>>> cfa1fb384365aa25a885bf6327d102d7924bf8d5
            links_context = await extract_and_fetch_links(message.content)
            full_prompt = message.content
            if links_context:
                full_prompt += links_context

<<<<<<< HEAD
=======
            # 4. Sprawdzanie załączników (obrazków)
>>>>>>> cfa1fb384365aa25a885bf6327d102d7924bf8d5
            images = []
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    img_part = await download_image_as_part(attachment.url, attachment.content_type)
                    if img_part:
                        images.append(img_part)

<<<<<<< HEAD
            # Bezpieczne wywołanie - przekazujemy argumenty zgodnie z nową strukturą w gemini.py
            if images:
                raw_response = generateResponseGemini(prompt=full_prompt, image_parts=images)
            else:
                raw_response = generateResponseGemini(prompt=full_prompt)
=======
            # 5. Wywołanie Twojej funkcji Gemini (zakładam, że przyjmuje prompt i listę obrazów)
            # Jeśli funkcja nie obsługuje obrazów, przekaż sam full_prompt
            if images:
                # Przykład, jeśli funkcja przyjmuje listę obiektów Part:
                raw_response = generateResponseGemini([full_prompt] + images)
            else:
                raw_response = generateResponseGemini(full_prompt)

            # Opcjonalnie: obsługa asynchroniczna, jeśli generateResponseGemini wymaga 'await'
            # raw_response = await generateResponseGemini(...)
>>>>>>> cfa1fb384365aa25a885bf6327d102d7924bf8d5

            if not raw_response:
                await message.reply("Przepraszam, nie udało mi się wygenerować odpowiedzi.")
                return

<<<<<<< HEAD
=======
            # 6. Dzielenie wiadomości na części (Discord ma limit 2000 znaków) i wysyłanie
>>>>>>> cfa1fb384365aa25a885bf6327d102d7924bf8d5
            chunks = split_message(raw_response)
            for chunk in chunks:
                await message.reply(chunk)

        except Exception as e:
            print(f"[Błąd on_message]: {e}")
            await message.reply("Wystąpił nieoczekiwany błąd podczas przetwarzania wiadomości.")

<<<<<<< HEAD
client.run(TOKEN)
=======
client.run(TOKEN)
>>>>>>> cfa1fb384365aa25a885bf6327d102d7924bf8d5
