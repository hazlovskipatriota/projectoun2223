import os
import re
import io
import datetime
import discord
import aiohttp
from aiohttp import web
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.genai import types

from gemini import generateResponseGemini, generateImageImagen

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# Włączamy intencje domyślne oraz treść wiadomości i członków serwera (wymagane do moderacji i timeoutu)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)


async def download_image_as_part(url: str, mime_type: str) -> types.Part:
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return types.Part.from_bytes(data=data, mime_type=mime_type)
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
                links_context += f"Link: {url} (Nie udało się pobrać zawartości: {e})\n"
    return links_context


async def fetch_reply_chain(message: discord.Message, channel, max_depth=10):
    chain = []
    current_msg = message
    depth = 0
    while current_msg.reference and current_msg.reference.message_id and depth < max_depth:
        try:
            ref_id = current_msg.reference.message_id
            parent_msg = await channel.fetch_message(ref_id)
            chain.append(parent_msg)
            current_msg = parent_msg
            depth += 1
        except Exception as e:
            print(f"Przerwano pobieranie łańcucha na głębokości {depth}: {e}")
            break
    chain.reverse()
    return chain


@client.event
async def on_ready():
    print(f"Zalogowano jako {client.user}")

    if not hasattr(client, "web_started"):
        client.web_started = True
        await start_webserver()


@client.event
async def on_message(message: discord.Message):
    # Ignoruj własne wiadomości oraz boty
    if message.author == client.user or message.author.bot:
        return

    is_main_channel = (message.channel.id == CHANNEL_ID)

    try:
        context_prompt = ""
        image_parts = []
        links_to_fetch = ""

        # Jeżeli jesteśmy poza głównym kanałem, dodajemy ukrytą instrukcję dla bota, 
        # by nie odzywał się bez wyraźnej potrzeby moderacyjnej.
        if not is_main_channel:
            context_prompt += (
                "[WSKAZÓWKA SYSTEMOWA - WAŻNE]\n"
                "Ta wiadomość pochodzi z kanału pobocznego. Analizujesz ją wyłącznie pod kątem moderacji. "
                "Jeżeli uznasz wypowiedź użytkownika za szkodliwą lub wrogą, nałóż na niego TIMEOUT za pomocą tagu i surowo go poucz. "
                "Jeżeli jednak ta wiadomość jest zwyczajna, neutralna lub przyjazna, ODPOWIEDZ CAŁKOWITYM MILCZENIEM (nie pisz nic, dosłownie pusta odpowiedź).\n\n"
            )

        # 1. Budowanie pełnego łańcucha odpowiedzi (od najstarszej)
        reply_chain = await fetch_reply_chain(message, message.channel)
        
        if reply_chain:
            context_prompt += "[ŁAŃCUCH POPRZEDNICH WIADOMOŚCI W TYM WĄTKU]\n"
            for msg in reply_chain:
                context_prompt += f"ID: {msg.id} | {msg.author.display_name}: \"{msg.content}\"\n"
                for att in msg.attachments:
                    if att.content_type and att.content_type.startswith(("image/", "video/gif")):
                        part = await download_image_as_part(att.url, att.content_type)
                        if part:
                            image_parts.append(part)
                links_to_fetch += await extract_and_fetch_links(msg.content)
            context_prompt += "[KONIEC ŁAŃCUCHA]\n\n"

        # 2. Obsługa bieżącej wiadomości i jej załączników
        for att in message.attachments:
            if att.content_type and att.content_type.startswith(("image/", "video/gif")):
                context_prompt += f"[Użytkownik załączył do bieżącej wiadomości obraz/gif: {att.filename}]\n"
                part = await download_image_as_part(att.url, att.content_type)
                if part:
                    image_parts.append(part)

        links_to_fetch += await extract_and_fetch_links(message.content)
        if links_to_fetch:
            context_prompt += links_to_fetch + "\n"

        # Dane bieżącej wiadomości
        context_prompt += (
            f"ID bieżącej wiadomości: {message.id}\n"
            f"Autor bieżącej wiadomości: {message.author.display_name}\n"
            f"Treść bieżącej wiadomości: {message.content}"
        )

        # Jeśli to kanał poboczny, nie pokazujemy animacji pisania, póki nie zdecydujemy czy bot faktycznie odpowiada.
        if is_main_channel:
            async with message.channel.typing():
                response = generateResponseGemini(context_prompt, image_parts=image_parts)
        else:
            response = generateResponseGemini(context_prompt, image_parts=image_parts)
            # Na kanałach pobocznych interesuje nas reakcja tylko jeśli Gemini wygeneruje tag TIMEOUT lub REACT
            if not any(tag in response for tag in ["[TIMEOUT", "[REACT", "[GENERATE_IMAGE"]):
                return  # Ignorujemy i milczymy

        # Rozpoczynamy procedurę wysyłania na kanale pobocznym (skoro bot podjął decyzję o reakcji)
        channel_context = message.channel.typing() if not is_main_channel else None
        if channel_context:
            await channel_context.__aenter__()

        try:
            # 3. Obsługa tagu kary: [TIMEOUT:message=ID,seconds=SEC]
            timeout_pattern = r"\[TIMEOUT:message=(\d+),seconds=(\d+)\]"
            timeout_match = re.search(timeout_pattern, response, re.IGNORECASE)
            
            if timeout_match:
                target_msg_id = int(timeout_match.group(1))
                seconds = int(timeout_match.group(2))
                # Bezpieczne ograniczenie czasu kary zgodnie z życzeniem (od 10 sekund do 60 sekund)
                seconds = max(10, min(60, seconds))

                try:
                    target_message = await message.channel.fetch_message(target_msg_id)
                    member = target_message.author
                    
                    if isinstance(member, discord.Member):
                        # Nadanie kary wyciszenia (timeout)
                        duration = datetime.timedelta(seconds=seconds)
                        await member.timeout(duration, reason="Szkodliwe zachowanie potępione przez Stepana Banderę.")
                        print(f"Wyciszono użytkownika {member.display_name} na {seconds} sekund.")
                    else:
                        print("Nie można nałożyć timeoutu - użytkownik nie jest członkiem serwera (np. DM).")
                except Exception as timeout_error:
                    print(f"Nie udało się nałożyć timeoutu: {timeout_error}")

                response = re.sub(timeout_pattern, "", response, flags=re.IGNORECASE).strip()

            # 4. Obsługa tagu reakcji: [REACT:message=ID,emoji1,emoji2...]
            react_pattern = r"\[REACT:message=(\d+),([^\]]+)\]"
            react_match = re.search(react_pattern, response, re.IGNORECASE)

            if react_match:
                target_message_id = int(react_match.group(1))
                emojis_raw = react_match.group(2)
                emojis = [e.strip() for e in emojis_raw.split(",") if e.strip()]

                try:
                    target_message = await message.channel.fetch_message(target_message_id)
                    for emoji in emojis:
                        try:
                            await target_message.add_reaction(emoji)
                        except discord.HTTPException as r_err:
                            print(f"Błąd dodawania reakcji '{emoji}': {r_err}")
                except Exception as fetch_err:
                    print(f"Nie znaleziono wiadomości do reakcji ID {target_message_id}: {fetch_err}")

                response = re.sub(react_pattern, "", response, flags=re.IGNORECASE).strip()

            # 5. Obsługa tagu generowania obrazu: [GENERATE_IMAGE: prompt]
            image_pattern = r"\[GENERATE_IMAGE:(.*?)\]"
            image_match = re.search(image_pattern, response, re.IGNORECASE)
            image_bytes = None

            if image_match:
                image_prompt = image_match.group(1).strip()
                image_bytes = generateImageImagen(image_prompt)
                response = re.sub(image_pattern, "", response, flags=re.IGNORECASE).strip()

            # 6. Wysłanie odpowiedzi
            file_to_send = None
            if image_bytes:
                file_to_send = discord.File(io.BytesIO(image_bytes), filename="propaganda.jpg")

            chunks = [response[i:i+2000] for i in range(0, len(response), 2000) if response[i:i+2000].strip()]
            
            if not chunks and file_to_send:
                await message.reply(file=file_to_send)
            else:
                for idx, chunk in enumerate(chunks):
                    if idx == 0 and file_to_send:
                        await message.reply(chunk, file=file_to_send)
                    else:
                        await message.reply(chunk)

        finally:
            if channel_context:
                await channel_context.__aexit__(None, None, None)

    except Exception as e:
        # Odpowiadaj o błędzie tylko na kanale głównym, aby uniknąć spamu na innych kanałach
        if is_main_channel:
            await message.reply(f"Wystąpił błąd podczas przetwarzania: `{e}`")

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
    print(f"HTTP server listening on port {port}")

client.run(TOKEN)