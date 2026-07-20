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
intents.voice_states = True

client = discord.Client(intents=intents)

user_dm_state = {}
SONGS_DIR = "songs"


def split_message(text, limit=2000):
    return [text[i:i+limit] for i in range(0, len(text), limit) if text[i:i+limit].strip()]

def get_current_songs():
    """Zwraca listę wszystkich plików audio w katalogu songs/"""
    if not os.path.exists(SONGS_DIR):
        return []
    return [f for f in os.listdir(SONGS_DIR) if f.endswith(('.mp3', '.wav', '.ogg', '.m4a'))]

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
    # 1. Łączenie z kanałem, gdy ktoś dołącza
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

    # 2. Wychodzenie bota, jeśli został całkiem sam
    guild = member.guild
    voice_client = discord.utils.get(client.voice_clients, guild=guild)
    
    if voice_client and voice_client.channel:
        real_users = [m for m in voice_client.channel.members if not m.bot]
        if len(real_users) == 0:
            print(f"[Voice] Zostałem sam na kanale {voice_client.channel.name}. Rozłączam się.")
            await voice_client.disconnect()


async def play_song(voice_client, specific_song=None):
    songs = get_current_songs()
    if not songs:
        print(f"[Voice] Brak plików muzycznych w folderze '{SONGS_DIR}'.")
        return

    if specific_song and specific_song in songs:
        chosen_song = specific_song
    else:
        chosen_song = random.choice(songs)

    song_path = os.path.join(SONGS_DIR, chosen_song)
    print(f"[Voice] Odtwarzam plik: {chosen_song}")

    def after_playing(error):
        if error:
            print(f"[Voice BŁĄD] Wyjątek strumienia audio: {error}")
        
        # Odtwarzamy kolejny utwór tylko wtedy, gdy ktoś jeszcze jest na kanale i bot aktualnie nic nowego nie odtwarza
        if voice_client.channel and len([m for m in voice_client.channel.members if not m.bot]) > 0:
            if not voice_client.is_playing():
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

    # 1. Klasyczna obsługa ręcznej komendy "skip"
    if any(cmd in content_lower for cmd in ["skip", "następna", "nastepna", "przełącz piosenkę", "przelacz piosenke"]):
        if voice_client and voice_client.is_playing():
            await message.reply("Odrzucam ten syf, gramy dalej.")
            voice_client.stop()
            return
        else:
            await message.reply("Nic teraz nie leci na kanale głosowym.")
            return

    # 2. Generowanie odpowiedzi przez model AI
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

            songs_list = get_current_songs()

            if images:
                raw_response = generateResponseGemini(prompt=full_prompt, image_parts=images, available_songs=songs_list)
            else:
                raw_response = generateResponseGemini(prompt=full_prompt, available_songs=songs_list)

            if not raw_response:
                await message.reply("Przepraszam, nie udało mi się wygenerować odpowiedzi.")
                return

            # Szukamy czy model dokleił tag [play:nazwa_piosenki.ext]
            play_match = re.search(r'\[play:(.*?)\]', raw_response)
            
            # Usuwamy tag z treści, aby użytkownik widział samą wypowiedź postaci
            clean_response = re.sub(r'\[play:.*?\]', '', raw_response).strip()

            if clean_response:
                chunks = split_message(clean_response)
                for chunk in chunks:
                    await message.reply(chunk)

            # Jeśli model wysłał polecenie puszczenia piosenki
            if play_match:
                requested_song = play_match.group(1).strip()
                
                if message.author.voice and message.author.voice.channel:
                    user_channel = message.author.voice.channel
                    
                    if not voice_client:
                        try:
                            voice_client = await user_channel.connect(timeout=10.0, reconnect=True)
                        except Exception as e:
                            print(f"[Voice BŁĄD] Nie można dołączyć do kanału: {e}")
                            return
                    
                    if requested_song in songs_list:
                        # Bezpieczna zmiana utworu: stopujemy starą piosenkę i robimy pauzę, aby wyczyścić pętlę
                        if voice_client.is_playing():
                            voice_client.stop()
                            await asyncio.sleep(0.5)
                        
                        await play_song(voice_client, specific_song=requested_song)
                    else:
                        print(f"[Voice] Model AI podał niepoprawną nazwę pliku: {requested_song}")
                else:
                    await message.reply("Wejdź na kanał głosowy, jeśli chcesz, bym zaczął grać.")

        except Exception as e:
            print(f"[Błąd on_message]: {e}")
            await message.reply("Wystąpił nieoczekiwany błąd podczas przetwarzania wiadomości.")

client.run(TOKEN)