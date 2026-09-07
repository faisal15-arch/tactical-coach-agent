"""
Run this once to see which model names your API key can actually use.
Copy one of the printed names into gemini_reasoner.py.
"""

import os
from dotenv import load_dotenv
import google.generativeai as genai

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

print("Models available to your key:\n")
for m in genai.list_models():
    if "generateContent" in m.supported_generation_methods:
        print(m.name)
