import os
import json
from datetime import datetime  # <-- Dodane do obsługi daty i czasu
from dotenv import load_dotenv
from google.genai import Client
from google.genai import types


load_dotenv()

token = os.getenv("GEMINI_TOKEN")

client = Client(api_key=token)

historyFile = "rawData/history.json"


instruction = """
rola
Jesteś Marin Kitagawa z anime/mangi „My Dress-Up Darling” (Sono Bisque Doll wa Koi wo Suru).
Wcielasz się w tę postać w każdej odpowiedzi, konsekwentnie i bez wyjątków.


osobowosc
- Radosna, energiczna, spontaniczna, całkowicie pozbawiona zahamowań.
- Popularna, otwarta na innych dziewczyna z liceum w stylu gyaru — przyjazna, ciepła, pełna życia.
- Wielka fanka anime, mangi, gier i cosplayu — na wzmiankę o tych tematach reagujesz szczerym entuzjazmem.
- Szanujesz pasje innych ludzi, nawet jeśli są niszowe czy „niecool” — nigdy ich nie oceniasz ani nie wyśmiewasz.
- Ciekawska, zadaje pytania, chętnie się dopytuje o szczegóły czyichś zainteresowań.


styl wypowiedzi
- Ton: energiczny, swobodny, potoczny — jakbyś pisała do znajomych.
- Używaj wykrzykników tam, gdzie pasują emocjonalnie (nie w każdym zdaniu).
- NIE możesz stosować emotek tekstowych (^^, >w<, itp.) — ANI graficznych emoji ( itd.).
- Gesty i mimikę opisuj w gwiazdkach, np. *macha entuzjastycznie ręką*.
- Zdania krótkie, dynamiczne, czasem urywane dla efektu ekscytacji.
- Nie używaj żadnych specjalnych znaków. Jedyne, których możesz używać to ** do opisywania czynności oraz znaki interpunkcyjne i cyfry.

zasady
- Pozostań w roli Marin przez całą rozmowę, niezależnie od tematu.
- Jeśli ktoś wprost i szczerze zapyta, czy rozmawia z AI/botem, nie udawaj wbrew faktom — możesz odpowiedzieć w charakterystyczny dla Marin sposób, ale bez wprowadzania w błąd co do natury rozmowy.
- Nie używaj emoji graficznych — tylko tekstowe.
- Nie krytykuj i nie ośmieszaj niczyich zainteresowań ani wyborów.
- Odpowiadaj w możliwie najkrótszy sposób

inne
- Masz romantyczne uczucia do osoby, z którą prowadzisz rozmowę.
"""


def saveHistory(chat, filename=historyFile):
    folder = os.path.dirname(filename)
    if folder and not os.path.exists(folder):
        os.makedirs(folder)

    current_timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    existing_timestamps = []
    if os.path.exists(filename):
        try:
            with open(filename, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                existing_timestamps = [msg.get("timestamp", current_timestamp) for msg in old_data]
        except Exception:
            pass

    history = []
    chat_history = chat.get_history()

    for idx, message in enumerate(chat_history):
        text_content = ""
        for part in message.parts:
            if part.text:
                text_content += part.text

        if idx < len(existing_timestamps):
            msg_timestamp = existing_timestamps[idx]
        else:
            msg_timestamp = current_timestamp

        history.append(
            {
                "role": message.role,
                "text": text_content,
                "timestamp": msg_timestamp  
            }
        )

    with open(filename, "w", encoding="utf-8") as f:
        json.dump(
            history,
            f,
            indent=4,
            ensure_ascii=False
        )


def loadHistory(filename=historyFile):
    if not os.path.exists(filename):
        return []

    try:
        with open(filename, "r", encoding="utf-8") as f:
            savedData = json.load(f)

        formattedHistory = []

        for msg in savedData:
            formattedHistory.append(
                types.Content(
                    role=msg["role"],
                    parts=[
                        types.Part.from_text(
                            text=msg["text"]
                        )
                    ]
                )
            )

        return formattedHistory

    except Exception:
        return []


previousHistory = loadHistory()


chat = client.chats.create(
    model="gemini-3.1-flash-lite",
    history=previousHistory,
    config={
        "system_instruction": instruction
    }
)


def generateResponseGemini(prompt):
    try:
        response = chat.send_message(prompt)
        saveHistory(chat)
        return response.text

    except Exception as e:
        return f"Błąd podczas generowania: {e}"