import os
from dotenv import load_dotenv

def main():
    load_dotenv()
    print("--- Antigravity Scraper Iniciado ---")
    api_key = os.getenv("OPENROUTER_API_KEY")
    
    if not api_key:
        print("Error: No se encuentra la API Key en el archivo .env")
        return

    print("Configuración lista. Esperando instrucciones del Agente...")

if __name__ == "__main__":
    main()
