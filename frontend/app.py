import streamlit as st
import requests
import os

st.title("Scraper Frontend")

backend_url = os.getenv("BACKEND_URL", "http://backend:8000")

try:
    response = requests.get(f"{backend_url}/")
    if response.status_code == 200:
        st.success(f"Backend Connected! Response: {response.json()}")
    else:
        st.warning(f"Backend responded with {response.status_code}")
except Exception as e:
    st.error(f"Error connecting to backend at {backend_url}: {e}")
