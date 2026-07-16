import os
import re
import io
import discord
import aiohttp
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.genai import types

from gemini import generateResponseGemini, generateImageImagen

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


async def download_image_as_part(url: str, mime_type: str) -> types.Part:
    """Pobiera obraz z podanego URL i zwraca go jako obiekt Part dla Gemini API."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status == 200:
                data = await resp.read()
                return types.Part.from_bytes(data=data, mime_type=mime_type)
    return None


async def extract_and_fetch_links(text: str) -> str:
    """Wyszukuje linki w tekście, pobiera ich zawartość HTML i wyciąga czysty tekst."""
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
                        
                        # Usuwamy skrypty i style CSS
                        for script in soup(["script", "style"]):
                            script.decompose()
                            
                        title = soup.title.string.strip() if soup.title else "Brak tytułu strony"
                        # Pobieramy pierwsze 1500 znaków tekstu ze strony, żeby nie przepełnić kontekstu
                        page_text = " ".join(soup.get_text().split())[:1500]
                        
                        links_context += f"Link: {url}\nTytuł strony: {title}\nTreść strony (skrót): {page_text}\n---\n"
            except Exception as e:
                links_context += f"Link: {url} (Nie udało się pobrać zawartości: {e})\n"
    return links_context


async def fetch_reply_chain(message: discord.Message, channel, max_depth=10):
    """Rekurencyjnie pobiera łańcuch odpowiedzi wstecz."""
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
            print(f"Przerwano pobieranie łańcucha odpowiedzi na głębokości {depth}: {e}")
            break

    # Zwracamy od najstarszej do najnowszej
    chain.reverse()
    return chain


@client.event
async def on_ready():
    print(f"Zalogowano jako {client.user}")


@client.event
async def on_message(message: discord.Message):
    # Ignoruj własne wiadomości
    if message.author == client.user:
        return

    # Odpowiadaj tylko na wybranym kanale
    if message.channel.id != CHANNEL_ID:
        return

    # Pokaż, że bot "pisze"
    async with message.channel.typing():
        try:
            context_prompt = ""
            image_parts = []
            links_to_fetch = ""

            # 1. Budowanie pełnego łańcucha odpowiedzi (od najstarszej)
            reply_chain = await fetch_reply_chain(message, message.channel)
            
            if reply_chain:
                context_prompt += "[ŁAŃCUCH POPRZEDNICH WIADOMOŚCI W TYM WĄTKU]\n"
                for msg in reply_chain:
                    context_prompt += f"ID: {msg.id} | {msg.author.display_name}: \"{msg.content}\"\n"
                    
                    # Pobieranie obrazów ze starych wiadomości z wątku
                    for att in msg.attachments:
                        if att.content_type and att.content_type.startswith(("image/", "video/gif")):
                            part = await download_image_as_part(att.url, att.content_type)
                            if part:
                                image_parts.append(part)
                    
                    # Próba wyciągnięcia zawartości linków z historii
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

            # Dołączenie zebranych informacji o linkach do kontekstu
            if links_to_fetch:
                context_prompt += links_to_fetch + "\n"

            # Dane bieżącej wiadomości
            context_prompt += (
                f"ID bieżącej wiadomości: {message.id}\n"
                f"Autor bieżącej wiadomości: {message.author.display_name}\n"
                f"Treść bieżącej wiadomości: {message.content}"
            )

            # Generowanie odpowiedzi z Gemini
            response = generateResponseGemini(context_prompt, image_parts=image_parts)

            # 3. Obsługa tagu reakcji [REACT:message=ID,emoji1,emoji2...]
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
                        except discord.HTTPException as reaction_error:
                            print(f"Nie udało się dodać reakcji '{emoji}': {reaction_error}")
                except Exception as fetch_error:
                    print(f"Nie udało się odnaleźć wiadomości do reakcji o ID {target_message_id}: {fetch_error}")

                # Usunięcie tagu reakcji z tekstu
                response = re.sub(react_pattern, "", response, flags=re.IGNORECASE).strip()

            # 4. Obsługa tagu generowania obrazu [GENERATE_IMAGE: prompt]
            image_pattern = r"\[GENERATE_IMAGE:(.*?)\]"
            image_match = re.search(image_pattern, response, re.IGNORECASE)
            image_bytes = None

            if image_match:
                image_prompt = image_match.group(1).strip()
                print(f"Wykryto żądanie wygenerowania obrazu z promptem: {image_prompt}")
                
                # Wywołanie generatora Imagen
                image_bytes = generateImageImagen(image_prompt)
                
                # Usunięcie tagu generowania obrazu z tekstu wypowiedzi
                response = re.sub(image_pattern, "", response, flags=re.IGNORECASE).strip()

            # 5. Wysłanie odpowiedzi tekstowej oraz obrazu na Discorda
            # Jeśli wygenerowano obraz, wyślij go jako załącznik do pierwszej części wiadomości
            file_to_send = None
            if image_bytes:
                file_to_send = discord.File(io.BytesIO(image_bytes), filename="wygenerowany_obraz.jpg")

            # Discord ma limit 2000 znaków
            chunks = [response[i:i+2000] for i in range(0, len(response), 2000) if response[i:i+2000].strip()]
            
            if not chunks and file_to_send:
                # Jeśli tekst był pusty (np. bot usunął wszystkie tagi i nic nie napisał), wyślij sam obraz
                await message.reply(file=file_to_send)
            else:
                for idx, chunk in enumerate(chunks):
                    if idx == 0 and file_to_send:
                        # Załącznik dodajemy tylko do pierwszej części wysyłanej odpowiedzi
                        await message.reply(chunk, file=file_to_send)
                    else:
                        await message.reply(chunk)

        except Exception as e:
            await message.reply(f"Wystąpił błąd podczas przetwarzania: `{e}`")


client.run(TOKEN)