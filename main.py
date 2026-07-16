import os
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
            response = generateResponseGemini(message.content)

            # Discord ma limit 2000 znaków
            for i in range(0, len(response), 2000):
                await message.reply(response[i:i+2000])

        except Exception as e:
            await message.reply(f"Wystąpił błąd: `{e}`")


client.run(TOKEN)