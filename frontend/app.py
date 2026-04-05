import streamlit as st
import requests
import os
import pandas as pd
import datetime
import base64

st.set_page_config(page_title="Scraper de Informes Financieros", layout="wide")
st.title("📄 Scraper de Merlin Properties")

backend_url = os.getenv("BACKEND_URL", "http://backend:8000")

@st.cache_data(ttl=5) # Cache the data slightly to prevent spamming, but keep it fresh
def get_pdfs():
    try:
        response = requests.get(f"{backend_url}/api/pdfs")
        if response.status_code == 200:
            return response.json()
        return []
    except:
        return []

# --- Top Actions ---
if st.button("🔍 Iniciar Scraping de Merlin", type="primary"):
    with st.spinner("Ejecutando scraper con Playwright (esto puede tardar unos minutos)..."):
        try:
            res = requests.post(f"{backend_url}/run-scraper")
            if res.status_code == 200:
                data = res.json()
                st.success(f"¡Scraping completado! Descargados: **{data['downloaded_count']}**, Omitidos (ya existían): **{data['skipped_count']}**")
                # Invalidate the cache of get_pdfs so the UI refreshes immediately underneath
                get_pdfs.clear()
            else:
                st.error(f"Error en el backend: {res.status_code} - {res.text}")
        except Exception as e:
            st.error(f"Error de conexión: {e}")

st.divider()

# --- PDF Listing ---
st.subheader("📚 Informes Financieros Descargados")

pdfs = get_pdfs()

if not pdfs:
    st.info("No hay PDFs en la base de datos. Ejecuta el scraper para encontrar informes.")
else:
    # Build dataframe for nice display
    df = pd.DataFrame(pdfs)
    # Format the data
    df["download_date"] = pd.to_datetime(df["download_date"]).dt.strftime("%Y-%m-%d %H:%M")
    df["size_mb"] = (df["size_bytes"] / (1024 * 1024)).map("{:.2f} MB".format)
    
    # Display table showing basic info
    st.dataframe(df[["filename", "download_date", "size_mb"]], use_container_width=True, hide_index=True)
    
    st.divider()
    
    # --- PDF Viewer ---
    st.subheader("👁️ Visualizador de PDF")
    
    selected_filename = st.selectbox("Selecciona un informe para visualizar:", df["filename"].tolist())
    
    if selected_filename:
        # Instead of base64 encoding or fetching directly to frontend memory, we can use the StaticFiles mapping
        # from the backend. The URL would be: BACKEND_URL/pdfs/FILENAME
        # NOTE: Streamlit runs in the browser, so it needs to access the backend URL directly.
        # But if BACKEND_URL is an internal docker resolution (e.g. `http://backend:8000`), 
        # the user's browser won't resolve it. Let's assume Streamlit uses an iframe.
        # To avoid network mapping issues for the user, we can fetch the PDF bytes from backend directly
        # and embed them as base64 in the iframe, which acts as a robust standard viewer on all networks.
        
        with st.spinner("Cargando documento..."):
            try:
                pdf_res = requests.get(f"{backend_url}/pdfs/{selected_filename}")
                if pdf_res.status_code == 200:
                    base64_pdf = base64.b64encode(pdf_res.content).decode('utf-8')
                    pdf_display = f'<iframe src="data:application/pdf;base64,{base64_pdf}" width="100%" height="800" type="application/pdf"></iframe>'
                    st.markdown(pdf_display, unsafe_allow_html=True)
                else:
                    st.error(f"No se pudo cargar el archivo desde el servidor. Status: {pdf_res.status_code}")
            except Exception as e:
                st.error(f"Error cargando el PDF: {e}")
