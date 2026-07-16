import os
import re
import io
import discord
import aiohttp
from dotenv import load_dotenv
from google.genai import types

from gemini import generateResponseGemini

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
                # Gemini potrzebuje poprawnego mime_type (np. image/png, image/jpeg, image/gif)
                # Dla GIF-ów discord często przesyła je jako image/gif
                return types.Part.from_bytes(
                    data=data,
                    mime_type=mime_type
                )
    return None


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

            # Obsługa załączników (obrazów/gifów) w wiadomości, na którą bot odpowiada
            if message.reference and message.reference.message_id:
                try:
                    referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                    context_prompt += (
                        f"[KONTEKST DYSKUSJI]\n"
                        f"Użytkownik odpowiada na wiadomość o ID: {referenced_msg.id} wysłaną przez {referenced_msg.author.display_name}:\n"
                        f"\"{referenced_msg.content}\"\n"
                    )
                    
                    # Pobieranie obrazów z wiadomości referencyjnej
                    for att in referenced_msg.attachments:
                        if att.content_type and att.content_type.startswith(("image/", "video/gif")):
                            context_prompt += f"[Do tej wiadomości załączono obraz/gif o nazwie: {att.filename}]\n"
                            part = await download_image_as_part(att.url, att.content_type)
                            if part:
                                image_parts.append(part)
                                
                    context_prompt += "\n"
                except Exception as e:
                    print(f"Nie udało się pobrać wiadomości referencyjnej: {e}")

            # Obsługa załączników w bieżącej wiadomości
            for att in message.attachments:
                if att.content_type and att.content_type.startswith(("image/", "video/gif")):
                    context_prompt += f"[Użytkownik załączył do swojej wiadomości obraz/gif: {att.filename}]\n"
                    part = await download_image_as_part(att.url, att.content_type)
                    if part:
                        image_parts.append(part)

            # Dane bieżącej wiadomości tekstowej
            context_prompt += (
                f"ID bieżącej wiadomości: {message.id}\n"
                f"Autor bieżącej wiadomości: {message.author.display_name}\n"
                f"Treść: {message.content}"
            )

            # Generowanie odpowiedzi z uwzględnieniem zebranych obrazów
            response = generateResponseGemini(context_prompt, image_parts=image_parts)

            # Wyrażenie regularne do wyłapania tagu reakcji: [REACT:message=ID,emoji1,emoji2,...]
            react_pattern = r"\[REACT:message=(\d+),([^\]]+)\]"
            match = re.search(react_pattern, response, re.IGNORECASE)

            if match:
                target_message_id = int(match.group(1))
                emojis_raw = match.group(2)
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

                response = re.sub(react_pattern, "", response, flags=re.IGNORECASE).strip()

            # Discord ma limit 2000 znaków
            for i in range(0, len(response), 2000):
                chunk = response[i:i+2000]
                if chunk:
                    await message.reply(chunk)

        except Exception as e:
            await message.reply(f"Wystąpił błąd: `{e}`")


client.run(TOKEN)