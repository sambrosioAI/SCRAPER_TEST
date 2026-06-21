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
                            st.success(f"¡Scraping completado! Descargados: **{data['downloaded_count']}**, Omitidos (Duplicados o ya existentes): **{data['skipped_count']}**")
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
    
    # Formatear etiquetas de taxonomía para la tabla
    df["Área Solicitante"] = df["area_tag"].apply(lambda x: x.split("|")[0] if x and "|" in x else "")
    df["Empresa Estudiada"] = df["empresa_tag"].apply(lambda x: x.split("|")[0] if x and "|" in x else "")
    
    df.rename(columns={"filename": "Nombre del Archivo", "source_url": "URL de Origen"}, inplace=True)
    
    # Construir la columna de enlaces con el client-facing host
    df["Ver Documento"] = client_facing_backend + "/pdfs/" + df["Nombre del Archivo"]
    
    display_cols = ["Nombre del Archivo", "Área Solicitante", "Empresa Estudiada", "Tamaño (MB)", "Fecha de Descarga", "URL de Origen", "Ver Documento"]
    
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

    st.write("")
    st.write("")
    
    # --- UI DE ETIQUETADO DE METADATOS ---
    st.subheader("🏷️ Asignación de Etiquetas (SharePoint)")
    
    # Selector de documento
    pdf_names = [p["filename"] for p in pdfs]
    selected_filename = st.selectbox("Selecciona un PDF de la lista para editar etiquetas:", ["-- Selecciona un archivo --"] + pdf_names)
    
    if selected_filename != "-- Selecciona un archivo --":
        # Encontrar el elemento PDF correspondiente
        pdf_item = next(p for p in pdfs if p["filename"] == selected_filename)
        
        # Cargar términos de taxonomía del backend
        areas = []
        empresas = []
        try:
            r_area = requests.get(f"{backend_url}/api/taxonomy/area/terms")
            if r_area.status_code == 200:
                areas = r_area.json()
        except Exception as e:
            st.warning(f"No se pudieron cargar las áreas del TermStore: {e}")
            
        try:
            r_emp = requests.get(f"{backend_url}/api/taxonomy/empresa/terms")
            if r_emp.status_code == 200:
                empresas = r_emp.json()
        except Exception as e:
            st.warning(f"No se pudieron cargar las empresas del TermStore: {e}")
            
        col_tag_1, col_tag_2 = st.columns([1, 1])
        
        with col_tag_1:
            st.write("**Área Solicitante**")
            curr_area_label = ""
            if pdf_item.get("area_tag") and "|" in pdf_item["area_tag"]:
                curr_area_label = pdf_item["area_tag"].split("|")[0]
                
            area_options = ["-- Sin Asignar --"] + [f"{a['label']}|{a['id']}" for a in areas]
            
            def_idx_area = 0
            for idx, opt in enumerate(area_options):
                if opt.startswith(curr_area_label + "|"):
                    def_idx_area = idx
                    break
                    
            selected_area_opt = st.selectbox(
                "Selecciona el Área:", 
                options=area_options, 
                index=def_idx_area,
                format_func=lambda x: x.split("|")[0] if "|" in x else x,
                key="select_area_widget"
            )
            
        with col_tag_2:
            st.write("**Empresa Estudiada**")
            curr_emp_label = ""
            if pdf_item.get("empresa_tag") and "|" in pdf_item["empresa_tag"]:
                curr_emp_label = pdf_item["empresa_tag"].split("|")[0]
                
            emp_options = ["-- Sin Asignar --"] + [f"{e['label']}|{e['id']}" for e in empresas]
            
            def_idx_emp = 0
            for idx, opt in enumerate(emp_options):
                if opt.startswith(curr_emp_label + "|"):
                    def_idx_emp = idx
                    break
                    
            selected_emp_opt = st.selectbox(
                "Selecciona la Empresa:", 
                options=emp_options, 
                index=def_idx_emp,
                format_func=lambda x: x.split("|")[0] if "|" in x else x,
                key="select_empresa_widget"
            )
            
            # Formulario para crear una nueva etiqueta de Empresa
            with st.expander("➕ Crear nueva etiqueta de Empresa"):
                new_emp_name = st.text_input("Nombre de la nueva empresa:", key="new_emp_name_input")
                if st.button("Crear Término", key="create_term_btn_widget"):
                    if not new_emp_name.strip():
                        st.error("Por favor, introduce un nombre válido.")
                    else:
                        with st.spinner("Creando término en SharePoint..."):
                            try:
                                res_new_term = requests.post(f"{backend_url}/api/taxonomy/empresa/terms", json={"name": new_emp_name.strip()})
                                if res_new_term.status_code == 200:
                                    st.success(f"Empresa '{new_emp_name}' creada con éxito en el TermStore.")
                                    st.cache_data.clear()
                                    st.rerun()
                                else:
                                    st.error(f"Error creando término: {res_new_term.text}")
                            except Exception as ex:
                                st.error(f"Excepción: {ex}")
                                
        st.write("")
        if st.button("💾 Guardar Etiquetas de Metadatos", use_container_width=True, key="save_tags_btn_widget"):
            area_val = None if selected_area_opt == "-- Sin Asignar --" else selected_area_opt
            emp_val = None if selected_emp_opt == "-- Sin Asignar --" else selected_emp_opt
            
            with st.spinner("Guardando etiquetas..."):
                try:
                    res_save = requests.post(
                        f"{backend_url}/api/pdfs/{pdf_item['id']}/tags",
                        json={"area_tag": area_val, "empresa_tag": emp_val}
                    )
                    if res_save.status_code == 200:
                        save_data = res_save.json()
                        if save_data.get("sp_updated"):
                            st.success("¡Etiquetas guardadas con éxito en Base de Datos y en SharePoint!")
                        else:
                            st.warning(f"Etiquetas guardadas localmente. Nota: {save_data.get('sp_error')}")
                        st.cache_data.clear()
                        import time
                        time.sleep(1.5)
                        st.rerun()
                    else:
                        st.error(f"Error guardando etiquetas: {res_save.text}")
                except Exception as e_save:
                    st.error(f"Excepción al guardar: {e_save}")
