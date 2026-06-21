import httpx
import os
import asyncio
from dotenv import load_dotenv

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
        url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/drive"
        drive_resp = await client.get(url, headers=headers)
        drive_id = drive_resp.json()["id"]
        
        filename = "icade-s-2025-full-year-results.pdf"
        
        # Method 3: Resolve by path in the drive
        # The files are uploaded under documents > data > pdfs
        # Which translates to path: data/pdfs/{filename}
        path_url = f"https://graph.microsoft.com/v1.0/drives/{drive_id}/root:/data/pdfs/{filename}"
        params = {"$expand": "listItem"}
        resp = await client.get(path_url, headers=headers, params=params)
        print(f"Method 3 (Path) Status: {resp.status_code}")
        if resp.status_code == 200:
            data = resp.json()
            list_item = data.get("listItem", {})
            print(f"Drive Item ID: {data.get('id')}")
            print(f"List Item ID: {list_item.get('id')}")
        else:
            print(f"Method 3 Error: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
