import os
import re
import discord
from dotenv import load_dotenv

from gemini import generateResponseGemini

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)


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
            # Przygotowanie promptu z uwzględnieniem kontekstu i ID wiadomości do ewentualnych reakcji
            context_prompt = ""
            
            # Jeśli wiadomość jest odpowiedzią na inną wiadomość
            if message.reference and message.reference.message_id:
                try:
                    referenced_msg = await message.channel.fetch_message(message.reference.message_id)
                    context_prompt += (
                        f"[KONTEKST DYSKUSJI]\n"
                        f"Użytkownik odpowiada na wiadomość o ID: {referenced_msg.id} wysłaną przez {referenced_msg.author.display_name}:\n"
                        f"\"{referenced_msg.content}\"\n\n"
                    )
                except Exception as e:
                    print(f"Nie udało się pobrać wiadomości referencyjnej: {e}")

            # Dane bieżącej wiadomości
            context_prompt += (
                f"ID bieżącej wiadomości: {message.id}\n"
                f"Autor bieżącej wiadomości: {message.author.display_name}\n"
                f"Treść: {message.content}"
            )

            response = generateResponseGemini(context_prompt)

            # Wyrażenie regularne do wyłapania tagu reakcji: [REACT:message=ID,emoji1,emoji2,...]
            # Flaga re.IGNORECASE na wypadek, gdyby model napisał to małymi literami
            react_pattern = r"\[REACT:message=(\d+),([^\]]+)\]"
            match = re.search(react_pattern, response, re.IGNORECASE)

            if match:
                target_message_id = int(match.group(1))
                emojis_raw = match.group(2)
                # Rozbicie po przecinkach i usunięcie zbędnych spacji
                emojis = [e.strip() for e in emojis_raw.split(",") if e.strip()]

                # Próba dodania reakcji
                try:
                    # Pobieramy obiekt wiadomości, na którą należy nałożyć reakcje
                    target_message = await message.channel.fetch_message(target_message_id)
                    for emoji in emojis:
                        try:
                            await target_message.add_reaction(emoji)
                        except discord.HTTPException as reaction_error:
                            print(f"Nie udało się dodać reakcji '{emoji}': {reaction_error}")
                except Exception as fetch_error:
                    print(f"Nie udało się odnaleźć wiadomości do reakcji o ID {target_message_id}: {fetch_error}")

                # Usunięcie tagu [REACT:...] z ostatecznej odpowiedzi wysyłanej na czat
                response = re.sub(react_pattern, "", response, flags=re.IGNORECASE).strip()

            # Discord ma limit 2000 znaków
            for i in range(0, len(response), 2000):
                chunk = response[i:i+2000]
                if chunk:  # Upewniamy się, że nie wysyłamy pustego ciągu po usunięciu tagu
                    await message.reply(chunk)

        except Exception as e:
            await message.reply(f"Wystąpił błąd: `{e}`")


client.run(TOKEN)