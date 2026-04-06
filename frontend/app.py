import streamlit as st
import requests
import os
import pandas as pd
from urllib.parse import urlparse

st.set_page_config(page_title="Scraper Financiero", layout="wide")

# -- CSS Personalizado --
st.markdown("""
<style>
    .stButton>button {
        background-color: #2e6b4c;
        color: white;
        border-radius: 8px;
        padding: 0.5rem 2rem;
        font-weight: bold;
        transition: all 0.3s ease;
        border: none;
    }
    .stButton>button:hover {
        background-color: #1e4a33;
        transform: scale(1.02);
    }
</style>
""", unsafe_allow_html=True)

st.title("📄 Scraper Universal de Archivos PDF")

backend_url = os.getenv("BACKEND_URL", "http://backend:8000")
client_facing_backend = os.getenv("CLIENT_BACKEND_URL", "http://localhost:8000")

@st.cache_data(ttl=5)
def get_pdfs():
    try:
        response = requests.get(f"{backend_url}/api/pdfs")
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return []
    
@st.cache_data(ttl=5)
def get_targets():
    try:
        response = requests.get(f"{backend_url}/api/targets")
        if response.status_code == 200:
            return response.json()
    except:
        pass
    return []

# --- Base de Web Scraping ---
col1, col2 = st.columns([1, 1])

with col1:
    st.subheader("🌐 Añadir nueva url de rastreo")
    
    with st.form("scraper_form", clear_on_submit=True):
        target_url = st.text_input("URL Objetivo para el Scraper", value="", placeholder="Introduce la dirección web...")
        submitted = st.form_submit_button("🔍 Iniciar Scraping", type="primary")
        
        if submitted:
            parsed = urlparse(target_url)
            # Validar URL
            if not target_url.strip() or not parsed.scheme or not parsed.netloc:
                st.error("⚠️ Error: La URL proporcionada no es válida. Asegúrate de incluir http:// o https://")
            else:
                with st.spinner(f"Analizando {target_url} con Playwright (esto puede tardar unos minutos)..."):
                    try:
                        res = requests.post(f"{backend_url}/run-scraper", json={"url": target_url})
                        if res.status_code == 200:
                            data = res.json()
                            st.success(f"¡Scraping completado! Descargados: **{data['downloaded_count']}**, Omitidos (ya existían en BBDD): **{data['skipped_count']}**")
                            get_pdfs.clear()
                            get_targets.clear()
                        elif res.status_code == 400:
                            err_data = res.json()
                            st.error(f"⚠️ {err_data.get('error', 'Error reportado por el backend.')}")
                        else:
                            st.error(f"Error en el backend: {res.status_code} - {res.text}")
                    except Exception as e:
                        st.error(f"Error de conexión: {e}")

with col2:
    st.subheader("🕸️ Webs Rastreadas Anteriormente")
    targets_data = get_targets()
    if not targets_data:
        st.info("Aún no se ha rastreado ninguna URL base.")
    else:
        st.write("*(Gestiona y restrea tus enlaces históricos de forma perimetral)*")
        for t in targets_data:
            c_meta, c_btn_rescrape, c_btn_del = st.columns([4, 1, 1])
            with c_meta:
                date_str = pd.to_datetime(t["last_scraped_date"]).strftime("%Y-%m-%d %H:%M")
                st.markdown(f"**URL:** `{t['target_url']}`  \n**Archivos:** {t['total_pdfs_found']} &nbsp;&nbsp;|&nbsp;&nbsp; **Actualizado:** {date_str}")
            with c_btn_rescrape:
                if st.button("🚀", key=f"rescrape_{t['id']}", help="Volver a rastrear exclusivamente esta URL (Extraerá archivos nuevos omitiendo los que ya existan)"):
                    with st.spinner("Rastreando..."):
                        try:
                            res = requests.post(f"{backend_url}/run-scraper", json={"url": t['target_url']})
                            if res.status_code == 200:
                                get_pdfs.clear()
                                get_targets.clear()
                                st.rerun()
                            elif res.status_code == 400:
                                err_data = res.json()
                                st.error(f"⚠️ {err_data.get('error', 'Fallo')}")
                            else:
                                st.error(str(res.text))
                        except Exception as e:
                            st.error(str(e))
            with c_btn_del:
                if st.button("🗑️", key=f"del_{t['id']}", help="Elimina esta web y destruye físicamente sus PDFs"):
                    with st.spinner("Purgando enlace..."):
                        try:
                            res_del = requests.delete(f"{backend_url}/api/targets/{t['id']}")
                            if res_del.status_code == 200:
                                get_pdfs.clear()
                                get_targets.clear()
                                st.rerun()
                            else:
                                st.error(f"Error borrando: {res_del.status_code}")
                        except Exception as e:
                            st.error(e)
            st.divider()
        
        if st.button("🔄 Rastrear todas de nuevo", use_container_width=True):
            with st.spinner("Realizando rasterado de todas las URLs almacenadas. Esto va a tardar..."):
                try:
                    res_all = requests.post(f"{backend_url}/run-scraper-all")
                    if res_all.status_code == 200:
                        data_all = res_all.json()
                        st.success(f"¡Bulk completado! Descargados: {data_all['downloaded_count']}, Omitidos: {data_all['skipped_count']}. Fallidos: {len(data_all.get('errors', []))}")
                        get_pdfs.clear()
                        get_targets.clear()
                    else:
                        st.error(f"Error Bulk: {res_all.status_code}")
                except Exception as e:
                    st.error(e)


st.divider()

# --- PDF Listing ---
st.subheader("📚 Panel de Documentos")
st.write("Pulsa en los enlaces de la columna 'Ver Documento' para abrir cualquier informe directamente en el navegador.")

pdfs = get_pdfs()

if not pdfs:
    st.info("La base de datos está vacía. Ejecuta el scraper para encontrar documentos.")
else:
    df = pd.DataFrame(pdfs)
    
    # Preparar datos de la tabla principal
    df["Fecha de Descarga"] = pd.to_datetime(df["download_date"]).dt.strftime("%Y-%m-%d %H:%M")
    df["Tamaño (MB)"] = (df["size_bytes"] / (1024 * 1024)).map("{:.2f} MB".format)
    df.rename(columns={"filename": "Nombre del Archivo", "source_url": "URL de Origen"}, inplace=True)
    
    # Construir la columna de enlaces con el client-facing host
    df["Ver Documento"] = client_facing_backend + "/pdfs/" + df["Nombre del Archivo"]
    
    display_cols = ["Nombre del Archivo", "Tamaño (MB)", "Fecha de Descarga", "URL de Origen", "Ver Documento"]
    
    # Renderizamos en Streamlit con column_config para hacer los enlaces clickleables nativamente
    st.dataframe(
        df[display_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Ver Documento": st.column_config.LinkColumn(
                "Visor PDF Externo",
                display_text="Abrir PDF ↗",
                help="Abrirá el PDF en una nueva pestaña"
            )
        }
    )
