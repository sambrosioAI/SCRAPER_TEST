from fastapi import FastAPI
import os

app = FastAPI()

@app.get("/")
def read_root():
    api_key_status = "present" if os.getenv("OPENROUTER_API_KEY") else "missing"
    return {"message": "Hello from Playwright Backend!", "api_key": api_key_status}
