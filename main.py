from gemini import generateResponseGemini


while True:
    prompt = str(input("User: "))
    response = generateResponseGemini(prompt)
    print("Marin: ",response)