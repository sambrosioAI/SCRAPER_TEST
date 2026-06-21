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
    headers = {"Authorization": f"Bearer {token}"}
    
    async with httpx.AsyncClient() as client:
        # Get Site info and drive
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drive"
        drive_resp = await client.get(url, headers=headers)
        drive_id = drive_resp.json()["id"]
        
        # Get corresponding list
        list_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/list"
        list_resp = await client.get(list_url, headers=headers)
        list_id = list_resp.json()["id"]
        
        # List all columns including hidden ones via $select
        columns_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/columns"
        params = {"$select": "id,name,displayName,hidden"}
        cols_resp = await client.get(columns_url, headers=headers, params=params)
        cols_data = cols_resp.json().get("value", [])
        
        print(f"--- FILTERED COLUMNS FOR LIST {list_id} ---")
        for col in cols_data:
            name = col.get("name")
            display_name = col.get("displayName")
            is_hidden = col.get("hidden", False)
            name_lower = name.lower() if name else ""
            disp_lower = display_name.lower() if display_name else ""
            if any(k in name_lower or k in disp_lower for k in ["solicitante", "estudiada", "area", "empresa", "tax"]):
                print(f"Name: {name} | DisplayName: {display_name} | Hidden: {is_hidden}")

if __name__ == "__main__":
    asyncio.run(main())
