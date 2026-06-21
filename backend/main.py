import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.future import select
from sqlalchemy import text
from playwright.async_api import async_playwright
import httpx
import re
import random
import asyncio
from urllib.parse import urlparse
from crawlee.crawlers import PlaywrightCrawler, PlaywrightCrawlingContext

app = FastAPI(title="Merlin Scraper Backend")

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///scraper.db")
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
Base = declarative_base()

SHAREPOINT_SITE_URL = os.getenv("SHAREPOINT_SITE_URL")
SHAREPOINT_SITE_ID = os.getenv("SHAREPOINT_SITE_ID") # Optional: Direct ID for Sites.Selected
TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
# Standard internal name for "Documentos compartidos" is usually "Shared Documents"
SP_PDF_FOLDER = "Shared Documents/data/pdfs"
SP_DB_FOLDER = "Shared Documents/data"
LOCAL_DB_PATH = "scraper.db"

# Cache for SharePoint IDs to avoid redundant lookups under Sites.Selected
_SP_CACHE = {
    "site_id": SHAREPOINT_SITE_ID,
    "drive_id": None
}

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

async def get_sp_identifiers(client, headers):
    """
    Directly resolves Site ID and Drive ID without global searches.
    Crucial for 'Sites.Selected' permissions.
    """
    global _SP_CACHE
    
    if _SP_CACHE["site_id"] and _SP_CACHE["drive_id"]:
        return _SP_CACHE["site_id"], _SP_CACHE["drive_id"]

    # 1. Resolve Site ID if not provided
    if not _SP_CACHE["site_id"]:
        parsed = urlparse(SHAREPOINT_SITE_URL)
        hostname = parsed.hostname
        site_path = parsed.path.strip('/')
        
        # Format for direct lookup: sites/hostname:/sites/sitename
        # If it's a root site, it's just sites/hostname
        if site_path:
            lookup_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}:/{site_path}"
        else:
            lookup_url = f"https://graph.microsoft.com/v1.0/sites/{hostname}"
            
        print(f"[Graph] Resolving site ID via direct path: {lookup_url}")
        resp = await client.get(lookup_url, headers=headers)
        if resp.status_code != 200:
            print(f"Error resolving Site ID (Sites.Selected might require hardcoded SITE_ID): {resp.text}")
            resp.raise_for_status()
        
        _SP_CACHE["site_id"] = resp.json().get("id")

    # 2. Resolve Drive ID (Default Document Library)
    drive_url = f"https://graph.microsoft.com/v1.0/sites/{_SP_CACHE['site_id']}/drive"
    resp = await client.get(drive_url, headers=headers)
    if resp.status_code != 200:
        print(f"Error resolving Drive ID: {resp.text}")
        resp.raise_for_status()
    
    _SP_CACHE["drive_id"] = resp.json().get("id")
    
    return _SP_CACHE["site_id"], _SP_CACHE["drive_id"]

async def upload_to_sp(filename: str, content: bytes, folder_path: str) -> tuple:
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        
        async with httpx.AsyncClient() as client:
            _, drive_id = await get_sp_identifiers(client, headers)
            
            # Upload File directly
            graph_folder = folder_path.replace("Shared Documents/", "").replace("Documents/", "").strip("/")
            upload_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{filename}:/content"
            put_resp = await client.put(upload_url, headers=headers, content=content)
            put_resp.raise_for_status()
            
            drive_item = put_resp.json()
            drive_item_id = drive_item.get("id")
            
            # Resolve the listItem ID
            li_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/items/{drive_item_id}/listItem"
            li_resp = await client.get(li_url, headers=headers)
            list_item_id = None
            if li_resp.status_code == 200:
                list_item_id = li_resp.json().get("id")
                
            print(f"Archivo {filename} subido con éxito. DriveItem: {drive_item_id}, ListItem: {list_item_id}")
            return drive_item_id, list_item_id
    except Exception as e:
        print(f"Error subiendo {filename} a SharePoint: {e}")
        return None, None

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
    filename = Column(String, unique=True, index=True, nullable=False)
    source_url = Column(String, unique=True, index=True, nullable=False)
    download_date = Column(DateTime, default=datetime.utcnow)
    size_bytes = Column(Integer, nullable=False)
    parent_target_url = Column(String, index=True, nullable=True)
    drive_item_id = Column(String, nullable=True)
    list_item_id = Column(String, nullable=True)
    area_tag = Column(String, nullable=True)
    empresa_tag = Column(String, nullable=True)

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
            _, drive_id = await get_sp_identifiers(client, headers)
            
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
        
        # Migración dinámica: añadir nuevas columnas si no existen
        try:
            res = await conn.execute(text("PRAGMA table_info(scraped_pdfs);"))
            existing_cols = [row[1] for row in res.fetchall()]
            new_cols = {
                "drive_item_id": "TEXT",
                "list_item_id": "TEXT",
                "area_tag": "TEXT",
                "empresa_tag": "TEXT"
            }
            for col_name, col_type in new_cols.items():
                if col_name not in existing_cols:
                    print(f"Migrando BD: Añadiendo columna {col_name} a scraped_pdfs", flush=True)
                    await conn.execute(text(f"ALTER TABLE scraped_pdfs ADD COLUMN {col_name} {col_type};"))
        except Exception as e:
            print(f"Error ejecutando migración: {e}", flush=True)

# --- Pydantic Schemas ---

class ScrapeRequest(BaseModel):
    url: str
    area_tag: Optional[str] = None
    empresa_tag: Optional[str] = None

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
    drive_item_id: Optional[str] = None
    list_item_id: Optional[str] = None
    area_tag: Optional[str] = None
    empresa_tag: Optional[str] = None
    
    class Config:
        from_attributes = True

class TargetResponse(BaseModel):
    id: int
    target_url: str
    last_scraped_date: datetime
    total_pdfs_found: int

    class Config:
        from_attributes = True

async def update_sp_metadata(list_item_id: str, area_tag: Optional[str], empresa_tag: Optional[str]) -> tuple:
    """
    Updates the hidden taxonomy fields in SharePoint for a given list_item_id.
    Returns (success: bool, error: Optional[str])
    """
    if not list_item_id:
        return False, "No list_item_id provided"
    
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        async with httpx.AsyncClient() as client:
            _, drive_id = await get_sp_identifiers(client, headers)
            
            list_info_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/list"
            list_resp = await client.get(list_info_url, headers=headers)
            if list_resp.status_code == 200:
                list_id = list_resp.json().get("id")
                
                # Resolver columnas ocultas dinámicamente
                area_hidden = "b7fd4b1dee4d4886a868470f8808f500"  # fallback
                empresa_hidden = "n11a72e1dda14adca329b2b677e5c9a8"  # fallback
                
                cols_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/columns"
                cols_params = {"$select": "name,displayName,hidden"}
                cols_resp = await client.get(cols_url, headers=headers, params=cols_params)
                if cols_resp.status_code == 200:
                    cols_list = cols_resp.json().get("value", [])
                    for col in cols_list:
                        disp = col.get("displayName", "")
                        name = col.get("name", "")
                        if col.get("hidden") and disp == "Area solicitante_0":
                            area_hidden = name
                        elif col.get("hidden") and disp == "Empresa estudiada_0":
                            empresa_hidden = name
                            
                # Preparar el payload con las columnas ocultas correctas
                payload = {}
                if area_tag and "|" in area_tag:
                    label, guid = area_tag.split("|")
                    payload[area_hidden] = f"-1;#{label}|{guid}"
                if empresa_tag and "|" in empresa_tag:
                    label, guid = empresa_tag.split("|")
                    payload[empresa_hidden] = f"-1;#{label}|{guid}"
                    
                if payload:
                    fields_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/items/{list_item_id}/fields"
                    patch_resp = await client.patch(fields_url, json=payload, headers=headers)
                    if patch_resp.status_code == 200:
                        print(f"Etiquetas de SharePoint actualizadas con éxito vía Graph (campos ocultos).", flush=True)
                        return True, None
                    else:
                        err_msg = f"Graph returned status {patch_resp.status_code}: {patch_resp.text}"
                        print(f"Graph update failed for hidden fields: {err_msg}", flush=True)
                        return False, err_msg
                else:
                    return True, None
            return False, "Failed to resolve list_id"
    except Exception as e:
        err_msg = f"SharePoint Update Exception: {e}"
        print(err_msg, flush=True)
        return False, err_msg

# --- Scraper Logic ---

async def run_merlin_scraper(target_url: str, area_tag: Optional[str] = None, empresa_tag: Optional[str] = None) -> dict:
    downloaded = 0
    skipped = 0
    
    # 1. Validación Directa: Si es un PDF suelto, evitamos Playwright
    if target_url.lower().strip().endswith(".pdf"):
        async with AsyncSessionLocal() as session:
            # Check idempotency by URL
            result_idemp = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.source_url == target_url))
            if result_idemp.scalars().first():
                skipped += 1
            else:
                try:
                    filename = target_url.split("/")[-1].split("?")[0]
                    if not filename.lower().endswith(".pdf"):
                        filename = "document_direct.pdf"
                    
                    # Prevent filename collisions: Skip if filename already exists in DB
                    async with AsyncSessionLocal() as session_check:
                        name_check = await session_check.execute(select(ScrapedPDF).filter(ScrapedPDF.filename == filename))
                        if name_check.scalars().first():
                            print(f"Omitiendo {filename}: ya existe un archivo con este nombre.")
                            skipped += 1
                        else:
                            headers = {
                                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                                "Referer": target_url,
                            }
                            async with httpx.AsyncClient(verify=False) as client:
                                req = await client.get(target_url, headers=headers, timeout=60.0)
                                req.raise_for_status()
                                file_content = req.content
                                    
                            size_bytes = len(file_content)
                            
                            # Subir a SharePoint via Graph REST
                            drive_item_id, list_item_id = await upload_to_sp(filename, file_content, SP_PDF_FOLDER)
                            
                            # If tags are provided, update them in SharePoint immediately
                            if list_item_id and (area_tag or empresa_tag):
                                await update_sp_metadata(list_item_id, area_tag, empresa_tag)

                            new_pdf = ScrapedPDF(
                                filename=filename, 
                                source_url=target_url, 
                                size_bytes=size_bytes,
                                parent_target_url=target_url,
                                drive_item_id=drive_item_id,
                                list_item_id=list_item_id,
                                area_tag=area_tag,
                                empresa_tag=empresa_tag
                            )
                            session.add(new_pdf)
                            await session.commit()
                            downloaded += 1
                except Exception as e:
                    if "UNIQUE constraint failed" in str(e):
                        await session.rollback()
                        print("Conflicto de integridad en descarga directa evitado.")
                        skipped += 1
                    else:
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
        browser = await p.chromium.launch(headless=True, slow_mo=1000)
        fake_user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        context = await browser.new_context(
            user_agent=fake_user_agent,
            viewport={'width': 1920, 'height': 1080},
            ignore_https_errors=True # Ignorar problemas de certificados en bloqueos
        )
        page = await context.new_page()
        page.set_default_timeout(90000) # 90 segundos de margen
        
        print(f"Iniciando navegación cautelosa a: {target_url}")
        try:
            await page.goto(target_url, wait_until="networkidle", timeout=90000)
        except Exception as e:
            await browser.close()
            error_msg = str(e)
            # Extraer el nombre del dominio para el mensaje
            domain = urlparse(target_url).netloc.replace("www.", "").split(".")[0].capitalize()
            
            if "ERR_CONNECTION_REFUSED" in error_msg or "ERR_CONNECTION_CLOSED" in error_msg:
                raise Exception(f"⚠️ La web de destino ({domain}) ha rechazado la conexión. Es posible que nos hayan bloqueado temporalmente. Por favor, espera 15-30 minutos.")
            elif "Timeout" in error_msg:
                raise Exception(f"⚠️ La web {domain} está tardando demasiado en responder. Por favor, inténtalo más tarde.")
            else:
                raise Exception(f"⚠️ Error al acceder a {domain}: {error_msg}")
        
        # Pausa inicial humana
        await asyncio.sleep(random.uniform(2, 4))
        
        # --- DESPLEGAR ACORDEONES (Años) con pausas aleatorias ---
        years = [str(y) for y in range(2026, 2013, -1)]
        for year in years:
            try:
                # Buscar texto del año en botones o enlaces
                el = page.get_by_role("button", name=year, exact=False).or_(page.get_by_text(year, exact=True))
                if await el.count() > 0:
                    await el.first.click(force=True)
                    print(f"Desplegando año {year}...")
                    await asyncio.sleep(random.uniform(1.5, 3.5)) # Pausa aleatoria entre clics
            except:
                continue
                    
        await page.wait_for_timeout(3000)
        
        # Extract Links
        all_links = await page.locator("a").all()
        target_pdfs = []
        parsed_target = urlparse(target_url)
        base_domain = f"{parsed_target.scheme}://{parsed_target.netloc}"
        
        for link in all_links:
            try:
                href = await link.get_attribute("href")
                if href and ".pdf" in href.lower():
                    if not href.startswith("http"):
                        href = f"{base_domain}{href}" if href.startswith("/") else f"{base_domain}/{href}"
                    target_pdfs.append(href)
            except:
                continue
        
        # --- FALLBACK CRAWLEE (Potenciado) ---
        if len(target_pdfs) < 5: # Si el método nativo falla o encuentra muy poco
            print(f"Activando Rastreo Profundo con Crawlee (Encontrados nativos: {len(target_pdfs)})...")
            crawlee_pdfs = []
            
            # Configuramos el crawler para que sea más persistente
            crawler = PlaywrightCrawler(
                max_concurrency=1, # Muy lento para evitar ERR_CONNECTION_REFUSED
                headless=True,
                playwright_launch_options={"slow_mo": 2000},
            )
            
            @crawler.router.default_handler
            async def handler(ctx: PlaywrightCrawlingContext):
                # Desplegar todo lo posible antes de buscar
                buttons = await ctx.page.locator("button, a.accordion-toggle, div.v-icon").all()
                for btn in buttons[:10]: # Solo los primeros 10 para no saturar
                    try: await btn.click(timeout=1000)
                    except: pass
                
                # Buscar enlaces PDF
                links = await ctx.page.locator("a").all()
                for link in links:
                    href = await link.get_attribute("href")
                    if href and ".pdf" in href.lower():
                        if not href.startswith("http"):
                            href = f"{base_domain}{href}" if href.startswith("/") else f"{base_domain}/{href}"
                        crawlee_pdfs.append(href)
                
                # Seguir enlaces si estamos en el mismo dominio o subdominio
                await ctx.enqueue_links(regex=r".*\.pdf$")
                # Intentar también en subdominios financieros (común en Merlin/Icade)
                if "merlin" in target_url:
                    await ctx.enqueue_links(regex=r"https://ir\.merlinproperties\.com/.*")

            await crawler.run([target_url])
            target_pdfs.extend(crawlee_pdfs)
            target_pdfs = list(set(target_pdfs)) # Limpiar duplicados de URLs

        # --- DEDUPLICACIÓN FINAL POR NOMBRE ---
        target_pdfs = list(set(target_pdfs)) # Unique URLs
        unique_pdfs_map = {}
        for url in target_pdfs:
            fname = url.split("/")[-1].split("?")[0]
            if not fname.lower().endswith(".pdf"): fname = "document.pdf"
            if fname not in unique_pdfs_map:
                unique_pdfs_map[fname] = url
        
        final_list = list(unique_pdfs_map.values())
        print(f"Filtrado final: {len(target_pdfs)} URLs -> {len(final_list)} Archivos Únicos")

        async with AsyncSessionLocal() as session:
            for pdf_url in final_list:
                # 1. Idempotency check by URL
                res = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.source_url == pdf_url))
                if res.scalars().first():
                    skipped += 1
                    continue
                
                # 2. Idempotency check by Filename
                filename = pdf_url.split("/")[-1].split("?")[0]
                if not filename.lower().endswith(".pdf"): filename = f"doc_{downloaded}.pdf"
                
                res_name = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.filename == filename))
                if res_name.scalars().first():
                    print(f"Saltando duplicado por nombre: {filename}")
                    skipped += 1
                    continue

                try:
                    p_cookies = await context.cookies()
                    cookies_dict = {c['name']: c['value'] for c in p_cookies}
                    
                    headers = {
                        "User-Agent": fake_user_agent,
                        "Referer": target_url,
                        "Accept": "application/pdf,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
                    }
                    
                    async with httpx.AsyncClient(cookies=cookies_dict, verify=False) as client:
                        req = await client.get(pdf_url, headers=headers, timeout=60.0)
                        req.raise_for_status()
                        file_content = req.content
                    
                    drive_item_id, list_item_id = await upload_to_sp(filename, file_content, SP_PDF_FOLDER)
                    
                    # If tags are provided, update them in SharePoint immediately
                    if list_item_id and (area_tag or empresa_tag):
                        await update_sp_metadata(list_item_id, area_tag, empresa_tag)

                    session.add(ScrapedPDF(
                        filename=filename, 
                        source_url=pdf_url, 
                        size_bytes=len(file_content), 
                        parent_target_url=target_url,
                        drive_item_id=drive_item_id,
                        list_item_id=list_item_id,
                        area_tag=area_tag,
                        empresa_tag=empresa_tag
                    ))
                    await session.commit()
                    downloaded += 1
                except Exception as e:
                    # Catch integrity errors if filename unique constraint is hit
                    if "UNIQUE constraint failed" in str(e):
                        await session.rollback() # Crucial: Reset session state after integrity failure
                        print(f"Conflicto de integridad evitado para {filename}: ya existe.")
                        skipped += 1
                    else:
                        print(f"Error downloading {pdf_url}: {e}")
            
            # Final Update: Recalculate total unique PDFs for this target from DB
            count_stmt = select(text("count(*)")).select_from(text("scraped_pdfs")).where(text(f"parent_target_url=:turl"))
            count_res = await session.execute(count_stmt, {"turl": target_url})
            real_count = count_res.scalar()
            
            res_target = await session.execute(select(ScrapedTarget).filter(ScrapedTarget.target_url == target_url))
            target_obj = res_target.scalars().first()
            if target_obj:
                target_obj.last_scraped_date = datetime.utcnow()
                target_obj.total_pdfs_found = real_count
            else:
                session.add(ScrapedTarget(target_url=target_url, total_pdfs_found=real_count))
            
            await session.commit()

        await browser.close()
    
    sync_db_to_sp()
    return {"downloaded_count": downloaded, "skipped_count": (len(target_pdfs) - downloaded), "message": "Scraping complete"}

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
            _, drive_id = await get_sp_identifiers(client, headers)
            graph_folder = SP_PDF_FOLDER.replace("Shared Documents/", "").replace("Documents/", "").strip("/")
            download_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{filename}:/content"
            dl_resp = await client.get(download_url, headers=headers, follow_redirects=True)
            dl_resp.raise_for_status()
            return Response(content=dl_resp.content, media_type="application/pdf")
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Archivo no encontrado en SharePoint: {e}")

@app.post("/run-scraper", response_model=ScrapeResult)
async def api_run_scraper(req: ScrapeRequest):
    try:
        result = await run_merlin_scraper(req.url, req.area_tag, req.empresa_tag)
        return result
    except Exception as e:
        return JSONResponse(status_code=400, content={'error': str(e)})

@app.post("/run-scraper-all")
async def api_run_scraper_all():
    total_downloaded = 0
    skipped = 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedTarget))
        targets = result.scalars().all()
        urls = [t.target_url for t in targets]
    for url in urls:
        res = await run_merlin_scraper(url)
        total_downloaded += res["downloaded_count"]
        skipped += res["skipped_count"]
    return {"message": "Completado", "downloaded": total_downloaded, "skipped": skipped}

@app.get("/api/pdfs", response_model=List[PDFResponse])
async def list_pdfs():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedPDF).order_by(ScrapedPDF.download_date.desc()))
        return result.scalars().all()

@app.get("/api/targets", response_model=List[TargetResponse])
async def list_targets():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedTarget).order_by(ScrapedTarget.last_scraped_date.desc()))
        return result.scalars().all()

@app.delete("/api/targets/{target_id}")
async def delete_target(target_id: int):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(ScrapedTarget).filter(ScrapedTarget.id == target_id))
        target = res.scalars().first()
        if not target: raise HTTPException(status_code=404)
        
        pdfs_res = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.parent_target_url == target.target_url))
        pdfs = pdfs_res.scalars().all()
        
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient() as client:
            _, drive_id = await get_sp_identifiers(client, headers)
            graph_folder = SP_PDF_FOLDER.replace("Shared Documents/", "").replace("Documents/", "").strip("/")
            for pdf in pdfs:
                try:
                    url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/{graph_folder}/{pdf.filename}"
                    await client.delete(url, headers=headers)
                except: pass
                await session.delete(pdf)
        await session.delete(target)
        await session.commit()
    sync_db_to_sp()
    return {"message": "Purged"}

# --- Taxonomy & Tagging Endpoints ---

TERM_SET_AREA_ID = os.getenv("TERM_SET_AREA_ID", "f82e7dcc-cc3f-4fbe-ab58-bee9349392d5")
TERM_SET_EMPRESA_ID = os.getenv("TERM_SET_EMPRESA_ID", "0e00c022-929e-47f0-8be2-dce8362d2467")

class CreateTermRequest(BaseModel):
    name: str

class TagPDFRequest(BaseModel):
    area_tag: str = None  # Formatted as "Label|Guid"
    empresa_tag: str = None  # Formatted as "Label|Guid"

@app.get("/api/taxonomy/area/terms")
async def get_area_terms():
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/termStore/sets/{TERM_SET_AREA_ID}/terms"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            terms = resp.json().get("value", [])
            result = []
            for term in terms:
                term_id = term.get("id")
                labels = term.get("labels", [])
                default_label = next((l.get("name") for l in labels if l.get("isDefault")), "No Name")
                result.append({"id": term_id, "label": default_label})
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener áreas: {str(e)}")

@app.get("/api/taxonomy/empresa/terms")
async def get_empresa_terms():
    try:
        token = await get_graph_token()
        headers = {"Authorization": f"Bearer {token}"}
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/termStore/sets/{TERM_SET_EMPRESA_ID}/terms"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            terms = resp.json().get("value", [])
            result = []
            for term in terms:
                term_id = term.get("id")
                labels = term.get("labels", [])
                default_label = next((l.get("name") for l in labels if l.get("isDefault")), "No Name")
                result.append({"id": term_id, "label": default_label})
            return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al obtener empresas: {str(e)}")

@app.post("/api/taxonomy/empresa/terms")
async def create_empresa_term(req: CreateTermRequest):
    try:
        token = await get_graph_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/termStore/sets/{TERM_SET_EMPRESA_ID}/children"
        payload = {
            "labels": [
                {
                    "languageTag": "es-ES",
                    "name": req.name,
                    "isDefault": True
                }
            ]
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code in [200, 201]:
                term_data = resp.json()
                return {"id": term_data["id"], "label": req.name}
            else:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al crear empresa: {str(e)}")

@app.post("/api/pdfs/{pdf_id}/tags")
async def tag_pdf(pdf_id: int, req: TagPDFRequest):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(ScrapedPDF).filter(ScrapedPDF.id == pdf_id))
        pdf = result.scalars().first()
        if not pdf:
            raise HTTPException(status_code=404, detail="PDF no encontrado")
        
        pdf.area_tag = req.area_tag
        pdf.empresa_tag = req.empresa_tag
        await session.commit()
        
        sp_error = None
        
        # Si no tiene list_item_id o drive_item_id, lo resolvemos dinámicamente usando la ruta en el drive
        if not pdf.list_item_id or not pdf.drive_item_id:
            try:
                token = await get_graph_token()
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                async with httpx.AsyncClient() as client:
                    _, drive_id = await get_sp_identifiers(client, headers)
                    
                    path_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/data/pdfs/{pdf.filename}"
                    params = {"$expand": "listItem"}
                    resp = await client.get(path_url, headers=headers, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        pdf.drive_item_id = data.get("id")
                        pdf.list_item_id = data.get("listItem", {}).get("id")
                        await session.commit()
                        print(f"Resolved and saved drive_item_id ({pdf.drive_item_id}) and list_item_id ({pdf.list_item_id}) dynamically for {pdf.filename}", flush=True)
            except Exception as e:
                print(f"Error resolving list_item_id dynamically by path: {e}", flush=True)

        if pdf.list_item_id:
            success, err = await update_sp_metadata(pdf.list_item_id, req.area_tag, req.empresa_tag)
            if not success:
                sp_error = err
                
        sync_db_to_sp()
        return {
            "message": "Tags updated in local database.",
            "sp_updated": sp_error is None,
            "sp_error": sp_error
        }
