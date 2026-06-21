import httpx
import os
import asyncio
from dotenv import load_dotenv

# Load env file from parent directory
load_dotenv("../.env")

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SHAREPOINT_SITE_ID = os.getenv("SHAREPOINT_SITE_ID")

AREA_TERM_GUID = "5611f4d9-bf2b-40fb-8c22-0d261382d39f"
EMPRESA_TERM_GUID = "44c0271e-26a7-44ea-a8b8-b22ac94c6a22"

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
        return resp.json()["access_token"]

async def main():
    token = await get_graph_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    
    async with httpx.AsyncClient() as client:
        # Get Site info and drive
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drive"
        drive_resp = await client.get(url, headers=headers)
        drive_id = drive_resp.json()["id"]
        
        # Get corresponding list
        list_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/list"
        list_resp = await client.get(list_url, headers=headers)
        list_id = list_resp.json()["id"]
        
        # Get first PDF item
        items_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/items?expand=fields"
        items_resp = await client.get(items_url, headers=headers)
        items = items_resp.json().get("value", [])
        
        pdf_item = None
        for item in items:
            fields = item.get("fields", {})
            if fields.get("FileLeafRef", "").endswith(".pdf"):
                pdf_item = item
                break
                
        if not pdf_item:
            print("No PDF item found!")
            return
            
        item_id = pdf_item["id"]
        filename = pdf_item["fields"]["FileLeafRef"]
        print(f"Testing with PDF: {filename} (ID: {item_id})")
        
        # Test Graph payload using the real hidden column names:
        # b7fd4b1dee4d4886a868470f8808f500 -> Area solicitante_0
        # n11a72e1dda14adca329b2b677e5c9a8 -> Empresa estudiada_0
        payload = {
            "b7fd4b1dee4d4886a868470f8808f500": f"-1;#AOF|{AREA_TERM_GUID}",
            "n11a72e1dda14adca329b2b677e5c9a8": f"-1;#Icade|{EMPRESA_TERM_GUID}"
        }
        
        print("--- TRYING GRAPH PATCH WITH ACTUAL HIDDEN COLUMN NAMES ---")
        patch_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/items/{item_id}/fields"
        resp = await client.patch(patch_url, json=payload, headers=headers)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
