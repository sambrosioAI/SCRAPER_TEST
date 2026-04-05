import os
from datetime import datetime
from typing import List

from fastapi import FastAPI, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.future import select
from playwright.async_api import async_playwright
import httpx

app = FastAPI(title="Merlin Scraper Backend")

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

# --- Database Models ---

class ScrapedPDF(Base):
    __tablename__ = "scraped_pdfs"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True, nullable=False)
    source_url = Column(String, unique=True, index=True, nullable=False)
    download_date = Column(DateTime, default=datetime.utcnow)
    size_bytes = Column(Integer, nullable=False)

# Create tables if not exist
@app.on_event("startup")
async def startup():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# Ensure the local pdf directory exists with permissive writing rules
PDF_DIR = "/data/pdfs"
os.makedirs(PDF_DIR, exist_ok=True)
os.chmod(PDF_DIR, 0o777)

# Serve the PDF directory
app.mount("/pdfs", StaticFiles(directory=PDF_DIR), name="pdfs")

# --- Pydantic Schemas ---

class ScrapeResult(BaseModel):
    message: str
    downloaded_count: int
    skipped_count: int
    
class PDFResponse(BaseModel):
    id: int
    filename: str
    source_url: str
    download_date: datetime
    size_bytes: int
    
    class Config:
        orm_mode = True

# --- Scraper Logic ---

async def run_merlin_scraper() -> dict:
    url = "https://ir.merlinproperties.com/inversores/informacion-financiera/"
    downloaded = 0
    skipped = 0
    
    async with async_playwright() as p:
        # slow_mo: 1000 añade 1 segundo de retraso obligatorio entre cada acción de Playwright
        browser = await p.chromium.launch(headless=True, slow_mo=1000)
        
        # Simulamos ser un navegador real de Windows
        fake_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        context = await browser.new_context(
            accept_downloads=True,
            user_agent=fake_user_agent,
            viewport={'width': 1920, 'height': 1080}
        )
        page = await context.new_page()
        
        # Simulamos un timeout generoso
        await page.goto(url, wait_until="networkidle", timeout=60000)
        
        # Give it a bit more time to render initial JS links
        await page.wait_for_timeout(3000)
        print("Página inicial cargada. Empezando a desplegar años...")
        
        # Iterar sobre los años típicos en informes financieros para desplegar acordeones
        for year in range(2026, 2013, -1):  # Desde 2026 hasta 2014
            elements = await page.locator(f"text='{year}'").all()
            if elements:
                print(f"[{year}] Se encontraron {len(elements)} elementos con texto de este año.")
            for i, el in enumerate(elements):
                try:
                    if await el.is_visible():
                        print(f"[{year}] (idx {i}) Clic nativo en el elemento...")
                        await el.click(force=True, timeout=2000)
                        await page.wait_for_timeout(2000) # Wait for child content to render
                except Exception as e:
                    print(f"[{year}] (idx {i}) Clic nativo ha fallado. Intentando evaluar JS...")
                    try:
                        await el.evaluate("node => { try { node.click(); } catch(err) {} }")
                        await page.wait_for_timeout(2000)
                    except Exception as js_e:
                        print(f"[{year}] (idx {i}) Fallo total de clic: {js_e}")
                    
        # Esperamos a que todo termine de renderizar
        await page.wait_for_timeout(4000)
        print("Finalizada la expansión de acordeones. Buscando PDFs...")
        
        # Find all <a> tags blindly and check href inside Python to bypass strict CSS constraints
        all_links = await page.locator("a").all()
        target_pdfs = []
        for link in all_links:
            try:
                href = await link.get_attribute("href")
                if href and ".pdf" in href.lower():
                    if not href.startswith("http"):
                        # Handle relative URLs just in case
                        if href.startswith("/"):
                            href = f"https://ir.merlinproperties.com{href}"
                        else:
                            href = f"https://ir.merlinproperties.com/{href}"
                    target_pdfs.append(href)
            except Exception:
                continue
        
        # Filter duplicates
        target_pdfs = list(set(target_pdfs))
        print(f"Total de enlaces PDF únicos extraídos para chequear: {len(target_pdfs)}")
        
        # Si encuentra 0 enlaces, tomamos captura de depuración
        if len(target_pdfs) == 0:
            screenshot_path = os.path.join(PDF_DIR, "debug.png")
            print(f"Cero enlaces encontrados. Tomando captura en: {screenshot_path}")
            await page.screenshot(path=screenshot_path, full_page=True)

        async with AsyncSessionLocal() as session:
            for pdf_url in target_pdfs:
                # 1. Check idempotency
                result = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.source_url == pdf_url))
                existing = result.scalars().first()
                
                if existing:
                    skipped += 1
                    continue
                
                # 2. Download it via direct httpx
                try:
                    # Get the browser session cookies for auth/security
                    p_cookies = await context.cookies()
                    cookies_dict = {c['name']: c['value'] for c in p_cookies}
                    
                    filename = pdf_url.split("/")[-1].split("?")[0]
                    if not filename.lower().endswith(".pdf"):
                        filename = f"document_{downloaded}.pdf"
                        
                    save_path = os.path.join(PDF_DIR, filename)
                    
                    async with httpx.AsyncClient(cookies=cookies_dict, verify=False) as client:
                        headers = {"User-Agent": fake_user_agent}
                        print(f"Descargando directamente con httpx: {filename}")
                        req = await client.get(pdf_url, headers=headers, timeout=60.0)
                        req.raise_for_status()
                        
                        with open(save_path, "wb") as f:
                            f.write(req.content)
                            
                    size_bytes = os.path.getsize(save_path)
                    
                    # 3. Save to DB
                    new_pdf = ScrapedPDF(
                        filename=filename,
                        source_url=pdf_url,
                        size_bytes=size_bytes
                    )
                    session.add(new_pdf)
                    await session.commit()
                    downloaded += 1
                    print(f"Éxito: {filename} ({size_bytes} bytes)")
                except Exception as e:
                    print(f"Failed to direct download {pdf_url}: {e}")
                    
        await browser.close()
        
    return {"downloaded_count": downloaded, "skipped_count": skipped, "message": "Scraping complete"}


# --- Endpoints ---

@app.get("/")
def read_root():
    return {"message": "Playwright Merlin Scraper is Ready", "db_url": DATABASE_URL}

@app.post("/run-scraper", response_model=ScrapeResult)
async def api_run_scraper():
    result = await run_merlin_scraper()
    return result

@app.get("/api/pdfs", response_model=List[PDFResponse])
async def list_pdfs():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedPDF).order_by(ScrapedPDF.download_date.desc()))
        pdfs = result.scalars().all()
        return pdfs
