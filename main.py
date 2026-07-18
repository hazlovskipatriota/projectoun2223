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

TOKEN = os.getenv("DISCORD_TOKEN")[cite: 3]
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))[cite: 3]
LOG_CHANNEL_ID = 1528172889143119872

# Konfiguracja Firebase pobrana bezpośrednio ze zmiennych w .env
FIREBASE_PROJECT_ID = os.getenv("FIREBASE_PROJECT_ID")[cite: 3]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

client = discord.Client(intents=intents)

# Pamięć podręczna przechowująca stan blokad i promocji DM
user_dm_state = {}


async def get_games_from_firebase():
    """Pobiera asynchronicznie listę gier z kolekcji Firestore za pomocą publicznego REST API projektu"""
    # Adres URL struktury REST API Firestore dla publicznych danych w kolekcji 'games'
    url = f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}/databases/(default)/documents/games"[cite: 3]
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    documents = data.get("documents", [])
                    games_list = []
                    
                    for doc in documents:
                        fields = doc.get("fields", {})
                        
                        # Mapowanie struktury typów danych w Firestore REST API (np. stringValue)
                        title_field = fields.get("title") or fields.get("Title")
                        desc_field = fields.get("description") or fields.get("Description")
                        
                        title = title_field.get("stringValue") if title_field else "Nieznany Tytuł"
                        description = desc_field.get("stringValue") if desc_field else "Brak opisu."
                        
                        games_list.append({"title": title, "description": description})
                    
                    if games_list:
                        return games_list
    except Exception as e:
        print(f"Błąd podczas odczytu struktury Firestore REST API: {e}")
        
    # Rezerwowy fallback w przypadku błędu autoryzacji lub pustej bazy danych
    return [{"title": "Boku no Headshot: Resurrection", "description": "Dynamiczny shooter akcji stworzony dla prawdziwych wojowników."}]


async def send_log_transcript(user, content, direction="USER -> BOT"):
    """Przesyła zapis rozmowy w DM na wskazany w konfiguracji kanał tekstowy logów"""
    log_channel = client.get_channel(LOG_CHANNEL_ID)
    if log_channel:
        embed = discord.Embed(
            title=f"Transkrypcja DM [{direction}]",
            description=content,
            color=0x00ff00 if "BOT" in direction else 0x00aaff,
            timestamp=datetime.datetime.utcnow()
        )
        avatar_url = user.avatar.url if user.avatar else None
        embed.set_author(name=f"{user.name}", icon_url=avatar_url)
        try:
            await log_channel.send(embed=embed)
        except Exception as e:
            print(f"Nie udało się wysłać transkrypcji na kanał logów: {e}")


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


@tasks.loop(hours=24)
async def daily_game_promotion_task():
    """Wysyła raz dziennie wiadomość prywatną reklamującą losową grę z kolekcji Firestore games/"""
    await client.wait_until_ready()
    games = await get_games_from_firebase()
    if not games:
        return

    today = datetime.date.today()

    for guild in client.guilds:
        for member in guild.members:
            if member.bot:
                continue

            state = user_dm_state.setdefault(member.id, {"last_promo": None, "blocked_until": None})
            
            if state["last_promo"] == today:
                continue

            chosen_game = random.choice(games)
            promo_msg = (
                f"Sława! Odkryj produkcje z **UPA Games Launcher**!\n"
                f"Polecamy zagrać w: **{chosen_game.get('title')}**\n"
                f"Opis gry: *{chosen_game.get('description')}*\n"
                f"Uruchom swój UPA Games Launcher i ruszaj do walki!"
            )

            try:
                await member.send(promo_msg)
                state["last_promo"] = today
                await send_log_transcript(member, promo_msg, direction="BOT -> USER (PROMO)")
            except discord.Forbidden:
                print(f"Zablokowane wiadomości prywatne dla użytkownika: {member.display_name}")
            except Exception as e:
                print(f"Błąd wysyłania promocji: {e}")


@client.event
async def on_ready():
    print(f"Zalogowano jako {client.user}")
    
    if not daily_game_promotion_task.is_running():
        daily_game_promotion_task.start()

    if not hasattr(client, "web_started"):
        client.web_started = True
        await start_webserver()


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user or message.author.bot:
        return

    # REGUŁA: Bot całkowicie ignoruje jakiekolwiek skanowanie wiadomości na kanale logów transkrypcji
    if message.channel.id == LOG_CHANNEL_ID:
        return

    # ------------------------------------------------------------------------
    # SEKRECYJNA OBSŁUGA WIADOMOŚCI PRYWATNYCH (DM)
    # ------------------------------------------------------------------------
    if isinstance(message.channel, discord.DMChannel):
        user_id = message.author.id
        today = datetime.date.today()
        state = user_dm_state.setdefault(user_id, {"last_promo": None, "blocked_until": None})

        # Sprawdzenie, czy użytkownik ma zablokowaną konwersację na dzisiaj
        if state["blocked_until"] == today:
            return

        # Logowanie przychodzącej wiadomości od użytkownika
        await send_log_transcript(message.author, message.content, direction="USER -> BOT")

        # Krok A: Weryfikacja tematu wiadomości za pomocą Gemini
        validation_instruction = (
            "Jesteś surowym i dokładnym filtrem tematów konwersacji. Twoim jedynym zadaniem jest analiza tekstu. "
            "Jeśli wiadomość dotyczy gier komputerowych, tworzenia gier, mechanik growych, platformy UPA Games Launcher, "
            "bądź konkretnych tytułów gier – odpowiedz wyłącznie słowem 'TAK'. "
            "Jeśli tekst schodzi na jakikolwiek inny temat, pyta o sprawy prywatne, politykę, pogodę lub cokolwiek spoza świata gier – odpowiedz wyłącznie słowem 'NIE'."
        )
        validation_res = generateResponseGemini(
            prompt=f"Oceń intencję wiadomości: \"{message.content}\"",
            custom_instruction=validation_instruction
        )

        if "NIE" in validation_res.upper():
            state["blocked_until"] = today
            warn_response = "Wykryto temat niezwiązany z grami. Prowadzenie wiadomości prywatnych w dniu dzisiejszym zostaje zablokowane."
            await message.channel.send(warn_response)
            await send_log_transcript(message.author, warn_response, direction="BOT -> USER (BLOKADA)")
            return

        # Krok B: Wygenerowanie odpowiedzi w klimacie Stepana Bandery na temat gier
        dm_context_prompt = (
            f"[KONTEKST WIADOMOŚCI PRYWATNEJ - TEMATYKA GIER]\n"
            f"Rozmawiasz z użytkownikiem w wiadomościach prywatnych wyłącznie o grach (szczególnie z UPA Games Launcher). "
            f"Zachowaj swój rewolucyjny charakter narodowy i pamiętaj, by szanować Magyarország i używać tej nazwy.\n\n"
            f"Wiadomość użytkownika: {message.content}"
        )

        async with message.channel.typing():
            response = generateResponseGemini(dm_context_prompt)
            response = re.sub(r"\[TIMEOUT:[^\]]+\]", "", response)
            response = re.sub(r"\[REACT:[^\]]+\]", "", response)
            
            await message.channel.send(response)
            await send_log_transcript(message.author, response, direction="BOT -> USER (ODPOWIEDŹ)")
        return

    # ------------------------------------------------------------------------
    # STANDARDOWA OBSŁUGA KANAŁÓW PUBLICZNYCH NA SERWERZE
    # ------------------------------------------------------------------------
    is_main_channel = (message.channel.id == CHANNEL_ID)[cite: 3]

    try:
        context_prompt = ""
        image_parts = []
        links_to_fetch = ""

        if not is_main_channel:
            context_prompt += (
                "[WSKAZÓWKA SYSTEMOWA - WAŻNE]\n"
                "Ta wiadomość pochodzi z kanału pobocznego. Analizujesz ją wyłącznie pod kątem moderacji. "
                "Jeżeli uznasz wypowiedź użytkownika za szkodliwą lub wrogą, nałóż na niego TIMEOUT za pomocą tagu i surowo go poucz. "
                "Jeżeli jednak ta wiadomość jest zwyczajna, neutralna lub przyjazna, ODPOWIEDZ CAŁKOWITYM MILCZENIEM (nie pisz nic, dosłownie pusta odpowiedź).\n\n"
            )

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

        for att in message.attachments:
            if att.content_type and att.content_type.startswith(("image/", "video/gif")):
                context_prompt += f"[Użytkownik załączył do bieżącej wiadomości obraz/gif: {att.filename}]\n"
                part = await download_image_as_part(att.url, att.content_type)
                if part:
                    image_parts.append(part)

        links_to_fetch += await extract_and_fetch_links(message.content)
        if links_to_fetch:
            context_prompt += links_to_fetch + "\n"

        context_prompt += (
            f"ID bieżącej wiadomości: {message.id}\n"
            f"Autor bieżącej wiadomości: {message.author.display_name}\n"
            f"Treść bieżącej wiadomości: {message.content}"
        )

        if is_main_channel:
            async with message.channel.typing():
                response = generateResponseGemini(context_prompt, image_parts=image_parts)
        else:
            response = generateResponseGemini(context_prompt, image_parts=image_parts)
            if not any(tag in response for tag in ["[TIMEOUT", "[REACT", "[GENERATE_IMAGE"]):
                return

        channel_context = message.channel.typing() if not is_main_channel else None
        if channel_context:
            await channel_context.__aenter__()

        try:
            # Obsługa tagu kary: [TIMEOUT:message=ID,seconds=SEC]
            timeout_pattern = r"\[TIMEOUT:message=(\d+),seconds=(\d+)\]"
            timeout_match = re.search(timeout_pattern, response, re.IGNORECASE)
            
            citation_prefix = ""

            if timeout_match:
                target_msg_id = int(timeout_match.group(1))
                seconds = int(timeout_match.group(2))
                seconds = max(10, min(60, seconds))

                try:
                    target_message = await message.channel.fetch_message(target_msg_id)
                    member = target_message.author
                    
                    citation_content = target_message.content if target_message.content else "[Załącznik/Obraz]"
                    citation_prefix = (
                        f"**Wykryto wrogą działalność!**\n"
                        f">  **Autor:** {member.mention} ({member.display_name})\n"
                        f">  **Usunięta treść:** *{citation_content}*\n"
                        f" *Wyrok: Wyciszenie na {seconds} sekund.*\n\n"
                    )

                    try:
                        await target_message.delete()
                        print(f"Usunięto szkodliwą wiadomość o ID {target_msg_id}.")
                    except discord.Forbidden:
                        print("Brak uprawnień bota (Manage Messages) do usunięcia wiadomości!")
                    except Exception as delete_error:
                        print(f"Błąd podczas usuwania wiadomości: {delete_error}")

                    if isinstance(member, discord.Member):
                        duration = datetime.timedelta(seconds=seconds)
                        await member.timeout(duration, reason="Szkodliwe zachowanie potępione przez Stepana Banderę.")
                        print(f"Wyciszono użytkownika {member.display_name} na {seconds} sekund.")
                    else:
                        print("Nie można nałożyć timeoutu - użytkownik nie jest na serwerze.")

                except Exception as timeout_error:
                    print(f"Nie udało się pobrać wiadomości lub nałożyć timeoutu: {timeout_error}")

                response = re.sub(timeout_pattern, "", response, flags=re.IGNORECASE).strip()

            # Obsługa tagu reakcji: [REACT:message=ID,emoji1,emoji2...]
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

            # Obsługa tagu generowania obrazu: [GENERATE_IMAGE: prompt]
            image_pattern = r"\[GENERATE_IMAGE:(.*?)\]"
            image_match = re.search(image_pattern, response, re.IGNORECASE)
            image_bytes = None

            if image_match:
                image_prompt = image_match.group(1).strip()
                image_bytes = generateImageImagen(image_prompt)
                response = re.sub(image_pattern, "", response, flags=re.IGNORECASE).strip()

            final_response = citation_prefix + response

            file_to_send = None
            if image_bytes:
                file_to_send = discord.File(io.BytesIO(image_bytes), filename="propaganda.jpg")

            chunks = [final_response[i:i+2000] for i in range(0, len(final_response), 2000) if final_response[i:i+2000].strip()]
            
            if not chunks and file_to_send:
                await message.channel.send(file=file_to_send)
            else:
                for idx, chunk in enumerate(chunks):
                    if idx == 0 and file_to_send:
                        if timeout_match:
                            await message.channel.send(chunk, file=file_to_send)
                        else:
                            await message.reply(chunk, file=file_to_send)
                    else:
                        if timeout_match:
                            await message.channel.send(chunk)
                        else:
                            await message.reply(chunk)

        finally:
            if channel_context:
                await channel_context.__aexit__(None, None, None)

    except Exception as e:
        if is_main_channel:
            await message.reply(f"Wystąpił błąd podczas przetwarzania: `{e}`")[cite: 3]


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

client.run(TOKEN)[cite: 3]