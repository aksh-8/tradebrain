from google import genai
import os
from dotenv import load_dotenv

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents='Return ONLY this JSON: {"direction": "bullish", "confidence": "high", "reasoning": "test ok"}',
)
print(response.text)
