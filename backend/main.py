import os
from datetime import datetime
from typing import List

from fastapi import FastAPI, BackgroundTasks, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from office365.sharepoint.files.file import File
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.future import select
from sqlalchemy import text
from playwright.async_api import async_playwright
import httpx
import re
from urllib.parse import urlparse
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext

app = FastAPI(title="Merlin Scraper Backend")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///scraper.db")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

SHAREPOINT_SITE_URL = os.getenv("SHAREPOINT_SITE_URL")
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
# Standard internal name for "Documentos compartidos" is usually "Shared Documents"
SP_PDF_FOLDER = "Shared Documents/data/pdfs"
SP_DB_FOLDER = "Shared Documents/data"
LOCAL_DB_PATH = "scraper.db"

async def get_graph_token():
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json().get("access_token")

async def get_site_info(client, headers):
    parsed = urlparse(SHAREPOINT_SITE_URL)
    hostname = parsed.hostname
    site_path = parsed.path.strip('/')
    
    # Try 1: Direct path lookup
    site_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{site_path}"
    resp = await client.get(site_url, headers=headers)
    if resp.status_code == 200:
        return resp.json().get("id")
        
    # Try 2: Search fallback
    site_name = site_path.split('/')[-1]
    search_url = f"https://graph.microsoft.com/v1.0/sites?search={site_name}"
    resp = await client.get(search_url, headers=headers)
    if resp.status_code == 200:
        results = resp.json().get("value", [])
        for site in results:
            if site_name.lower() in site.get("name", "").lower() or site_name.lower() in site.get("displayName", "").lower():
                return site.get("id")
    
    resp.raise_for_status()
    return None

async def upload_to_sp(filename: str, content: bytes, folder_path: str):
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient() as client:
            site_id = await get_site_info(client, headers)
            
            # 2. Get Drive ID
            drive_resp = await client.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive", headers=headers)
            drive_resp.raise_for_status()
            drive_id = drive_resp.json().get("id")
            
            # 3. Upload File
            graph_folder = folder_path.replace("Shared Documents/", "").replace("Documents/", "").strip("/")
            upload_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{filename}:/content"
            put_resp = await client.put(upload_url, headers=headers, content=content)
            put_resp.raise_for_status()
            
        print(f"Archivo {filename} subido con éxito a SharePoint (Graph REST).")
    except Exception as e:
        print(f"Error subiendo {filename} a SharePoint (Graph REST): {e}")

def sync_db_to_sp():
    # Helper to run async upload from sync context
    import asyncio
    try:
        with open(LOCAL_DB_PATH, "rb") as f:
            content = f.read()
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(upload_to_sp(LOCAL_DB_PATH, content, SP_DB_FOLDER))
        else:
            asyncio.run(upload_to_sp(LOCAL_DB_PATH, content, SP_DB_FOLDER))
    except Exception as e:
        print(f"Error en sync_db_to_sp: {e}")

# --- Database Models ---

class ScrapedPDF(Base):
    __tablename__ = "scraped_pdfs"
    
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True, nullable=False)
    source_url = Column(String, unique=True, index=True, nullable=False)
    download_date = Column(DateTime, default=datetime.utcnow)
    size_bytes = Column(Integer, nullable=False)
    parent_target_url = Column(String, index=True, nullable=True)

class ScrapedTarget(Base):
    __tablename__ = "scraped_targets"
    
    id = Column(Integer, primary_key=True, index=True)
    target_url = Column(String, unique=True, index=True, nullable=False)
    last_scraped_date = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    total_pdfs_found = Column(Integer, default=0)

# Create tables if not exist
@app.on_event("startup")
async def startup():
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient() as client:
            site_id = await get_site_info(client, headers)
            
            drive_resp = await client.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive", headers=headers)
            drive_resp.raise_for_status()
            drive_id = drive_resp.json().get("id")
            
            graph_folder = SP_DB_FOLDER.replace("Shared Documents/", "").replace("Documents/", "").strip("/")
            download_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{LOCAL_DB_PATH}:/content"
            
            dl_resp = await client.get(download_url, headers=headers, follow_redirects=True)
            if dl_resp.status_code == 200:
                with open(LOCAL_DB_PATH, "wb") as f:
                    f.write(dl_resp.content)
                print("Base de datos descargada de SharePoint (Graph REST).")
    except Exception as e:
        print(f"No se pudo descargar la base de datos de SharePoint (Graph REST): {e}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

# PDFs will be served directly from SharePoint

# --- Pydantic Schemas ---

class ScrapeRequest(BaseModel):
    url: str

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
        from_attributes = True

class TargetResponse(BaseModel):
    id: int
    target_url: str
    last_scraped_date: datetime
    total_pdfs_found: int

    class Config:
        from_attributes = True

# --- Scraper Logic ---

async def run_merlin_scraper(target_url: str) -> dict:
    downloaded = 0
    skipped = 0
    
    # 1. Validación Directa: Si es un PDF suelto, evitamos Playwright
    if target_url.lower().strip().endswith(".pdf"):
        async with AsyncSessionLocal() as session:
            # Check idempotency
            result_idemp = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.source_url == target_url))
            if result_idemp.scalars().first():
                skipped += 1
            else:
                try:
                    filename = target_url.split("/")[-1].split("?")[0]
                    if not filename.lower().endswith(".pdf"):
                        filename = "document_direct.pdf"
                        
                    async with httpx.AsyncClient(verify=False) as client:
                        req = await client.get(target_url, timeout=60.0)
                        req.raise_for_status()
                        file_content = req.content
                            
                    size_bytes = len(file_content)
                    
                    # Subir a SharePoint via Graph REST
                    await upload_to_sp(filename, file_content, SP_PDF_FOLDER)
                    
                    new_pdf = ScrapedPDF(
                        filename=filename, 
                        source_url=target_url, 
                        size_bytes=size_bytes,
                        parent_target_url=target_url
                    )
                    session.add(new_pdf)
                    await session.commit()
                    downloaded += 1
                except Exception as e:
                    raise Exception(f"Falla en descarga directa: {str(e)}")
                    
            # Upsert Target
            result_target = await session.execute(select(ScrapedTarget).filter(ScrapedTarget.target_url == target_url))
            existing_target = result_target.scalars().first()
            if existing_target:
                existing_target.last_scraped_date = datetime.utcnow()
                existing_target.total_pdfs_found += downloaded
            else:
                session.add(ScrapedTarget(target_url=target_url, total_pdfs_found=downloaded))
            await session.commit()
            
        sync_db_to_sp()
        return {"downloaded_count": downloaded, "skipped_count": skipped, "message": "Direct PDF download complete"}

    
    # Lógica con Playwright
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
        await page.goto(target_url, wait_until="networkidle", timeout=60000)
        
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
        
        from urllib.parse import urlparse
        parsed = urlparse(target_url)
        base_domain = f"{parsed.scheme}://{parsed.netloc}"
        
        # Find all <a> tags blindly
        all_links = await page.locator("a").all()
        target_pdfs = []
        
        for link in all_links:
            try:
                href = await link.get_attribute("href")
                if href and ".pdf" in href.lower():
                    if not href.startswith("http"):
                        # Handle relative URLs dynamically mapped to their parent origin
                        if href.startswith("/"):
                            href = f"{base_domain}{href}"
                        else:
                            href = f"{base_domain}/{href}"
                    target_pdfs.append(href)
            except Exception:
                continue
        
        # Filter duplicates
        target_pdfs = list(set(target_pdfs))
        print(f"Total de enlaces PDF únicos extraídos para chequear (Natívamente): {len(target_pdfs)}")
        
        # --- ESTRATEGIA DE FALLBACK CRAWLEE (DEEP SCAN) ---
        if len(target_pdfs) == 0:
            print("Playwright clásico no encontró PDFs. Iniciando Crawlee Deep Scan Fallback...")
            crawlee_pdfs = []
            
            # Instanciamos el Crawler nativo con los parámetros de optimización para Docker (2 workers)
            crawler = PlaywrightCrawler(
                max_concurrency=2,
                headless=True,
                browser_type_launch_options={
                    "args": ['--no-sandbox', '--disable-setuid-sandbox']
                }
            )
            
            @crawler.router.default_handler
            async def request_handler(ctx: PlaywrightCrawlingContext) -> None:
                url = ctx.request.url
                
                # Descarte rápido vía regex para procesar encolados documentales ofimáticos
                if re.search(r'\.(pdf|xlsx|xls|zip)$', url, re.IGNORECASE):
                    print(f"[Crawlee] DeepScan recolectó posible match: {url}")
                    # PERO estrictamente limitamos las descargas exclusivas a .pdf según el requerimiento
                    if url.lower().endswith(".pdf"):
                        crawlee_pdfs.append(url)
                    return
                
                # Lógica recursiva de penetración de acordeones y Deep Search (HTML Page)
                try:
                    page = ctx.page
                    await page.wait_for_load_state("networkidle", timeout=30000)
                    
                    # Forzar despliegue nativo mediante inyección JS
                    print(f"[Crawlee] Explotando menú en profundidad: {url}")
                    await page.evaluate("""() => {
                        const targets = document.querySelectorAll('button, div, span, a');
                        targets.forEach(el => {
                            const txt = el.innerText || '';
                            const cls = el.className || '';
                            if (txt.match(/20[1-3][0-9]/) || cls.match(/accordion|expand|v-icon|toggle/i)) {
                                try { el.click(); } catch (e) {}
                            }
                        });
                    }""")
                    
                    # Dar un respiro a que se carguen los nodos hijos e inyectar regex dinámicamente:
                    await page.wait_for_timeout(3500)
                except Exception as e_deep:
                    print(f"[Crawlee Error] Falló DeepScan parse JS en {url}: {e_deep}")
                
                # Una vez la página está expandida al máximo nivel, meteremos a cola Crawlee los enlaces relevantes
                await ctx.enqueue_links(
                    regex=r".*\.(pdf|xlsx|xls|zip)$"
                )
            
            # Encender el Scanner y aguardar resultados!
            await crawler.run([target_url])
            target_pdfs = list(set(crawlee_pdfs))
            print(f"Crawler Rescató orgánicamente {len(target_pdfs)} ENLACES validables PDF.")

        # Si tras ambos métodos sigue arrojando nulo, tomamos captura de depuración
        if len(target_pdfs) == 0:
            screenshot_path = "debug.png"
            print(f"Cero enlaces encontrados finalmente. Tomando captura en: {screenshot_path}")
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
                        
                    async with httpx.AsyncClient(cookies=cookies_dict, verify=False) as client:
                        headers = {"User-Agent": fake_user_agent}
                        print(f"Descargando directamente con httpx: {filename}")
                        req = await client.get(pdf_url, headers=headers, timeout=60.0)
                        req.raise_for_status()
                        file_content = req.content
                            
                    size_bytes = len(file_content)
                    
                    # Subir a SharePoint via Graph REST
                    await upload_to_sp(filename, file_content, SP_PDF_FOLDER)
                    
                    # 3. Save to DB
                    new_pdf = ScrapedPDF(
                        filename=filename,
                        source_url=pdf_url,
                        size_bytes=size_bytes,
                        parent_target_url=target_url
                    )
                    session.add(new_pdf)
                    await session.commit()
                    downloaded += 1
                    print(f"Éxito: {filename} ({size_bytes} bytes)")
                except Exception as e:
                    print(f"Failed to direct download {pdf_url}: {e}")
            
            # Upsert the Target Tracking history
            result_target = await session.execute(select(ScrapedTarget).filter(ScrapedTarget.target_url == target_url))
            existing_target = result_target.scalars().first()
            if existing_target:
                existing_target.last_scraped_date = datetime.utcnow()
                existing_target.total_pdfs_found += downloaded
            else:
                new_target = ScrapedTarget(
                    target_url=target_url,
                    total_pdfs_found=downloaded
                )
                session.add(new_target)
            await session.commit()
                    
        await browser.close()
        
    sync_db_to_sp()
    return {"downloaded_count": downloaded, "skipped_count": skipped, "message": "Scraping complete"}


# --- Endpoints ---

@app.get("/")
def read_root():
    return {"message": "Playwright Merlin Scraper is Ready", "db_url": DATABASE_URL}

@app.get("/pdfs/{filename}")
async def serve_pdf(filename: str):
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient() as client:
            site_id = await get_site_info(client, headers)
            
            drive_resp = await client.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive", headers=headers)
            drive_resp.raise_for_status()
            drive_id = drive_resp.json().get("id")
            
            graph_folder = SP_PDF_FOLDER.replace("Shared Documents/", "").replace("Documents/", "").strip("/")
            download_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{filename}:/content"
            
            dl_resp = await client.get(download_url, headers=headers, follow_redirects=True)
            dl_resp.raise_for_status()
            return Response(content=dl_resp.content, media_type="application/pdf")
    except Exception as e:
        print(f"Error sirviendo PDF {filename} (Graph REST): {e}")
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado en SharePoint: {e}")

@app.post("/run-scraper", response_model=ScrapeResult)
async def api_run_scraper(req: ScrapeRequest):
    try:
        result = await run_merlin_scraper(req.url)
        return result
    except Exception as e:
        print(f"Error scraping {req.url}: {e}")
        return JSONResponse(
            status_code=400, 
            content={'error': 'No se pudo procesar la URL. Asegúrate de que es una web válida o un enlace directo a PDF'}
        )

@app.post("/run-scraper-all")
async def api_run_scraper_all():
    total_downloaded = 0
    total_skipped = 0
    errors = []
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedTarget))
        targets = result.scalars().all()
        urls_to_scrape = [t.target_url for t in targets]
        
    for url in urls_to_scrape:
        try:
            res = await run_merlin_scraper(url)
            total_downloaded += res["downloaded_count"]
            total_skipped += res["skipped_count"]
        except Exception as e:
            print(f"Failed scraping {url} in bulk run: {e}")
            errors.append(url)
            
    return {
        "message": "Scraping masivo completado",
        "downloaded_count": total_downloaded,
        "skipped_count": total_skipped,
        "errors": errors
    }

@app.get("/api/pdfs", response_model=List[PDFResponse])
async def list_pdfs():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedPDF).order_by(ScrapedPDF.download_date.desc()))
        pdfs = result.scalars().all()
        return pdfs

@app.get("/api/targets", response_model=List[TargetResponse])
async def list_targets():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedTarget).order_by(ScrapedTarget.last_scraped_date.desc()))
        targets = result.scalars().all()
        return targets

@app.delete("/api/targets/{target_id}")
async def delete_target(target_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedTarget).filter(ScrapedTarget.id == target_id))
        target = result.scalars().first()
        if not target:
            raise HTTPException(status_code=404, detail="Target tracking not found")
        
        # Pull everything bound to this parent domain
        pdfs_result = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.parent_target_url == target.target_url))
        pdfs = pdfs_result.scalars().all()
        
        purged_files = 0
        try:
            token = await get_graph_token()
            headers = {"Authorization": f"Bearer {token}"}
            
            async with httpx.AsyncClient() as client:
                site_id = await get_site_info(client, headers)
                drive_resp = await client.get(f"https://graph.microsoft.com/v1.0/sites/{site_id}/drive", headers=headers)
                drive_resp.raise_for_status()
                drive_id = drive_resp.json().get("id")
                
                graph_folder = SP_PDF_FOLDER.replace("Shared Documents/", "").replace("Documents/", "").strip("/")

                for pdf in pdfs:
                    # 1. Unlink physical binary from SharePoint via Graph REST
                    try:
                        delete_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{pdf.filename}"
                        del_resp = await client.delete(delete_url, headers=headers)
                        # 204 No Content is success for DELETE
                        if del_resp.status_code in [200, 204]:
                            purged_files += 1
                        else:
                            print(f"Graph Delete failed for {pdf.filename}: {del_resp.status_code}")
                    except Exception as fe:
                        print(f"File physical kill failed on SP {pdf.filename}: {fe}")
                    
                    # 2. Obliterate from database relations
                    await session.delete(pdf)
        except Exception as e:
            print(f"Error global en purga de SharePoint: {e}")
            # Even if SP fails, we might want to continue deleting from DB, but 
            # let's stay safe and only delete from DB what we attempted to delete from SP.
            # In this case, the loop above handles DB deletion per file.
            
        # Obliterate actual domain tracker block 
        await session.delete(target)
        await session.commit()
        
    sync_db_to_sp()
    return {"message": "Cascade purge effective", "pdfs_deleted": purged_files, "target_freed": target.target_url}
