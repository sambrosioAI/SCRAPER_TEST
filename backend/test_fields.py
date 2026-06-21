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
        
        # Get items expanding fields (page size 100 to make sure we find them)
        items_url = f"https://graph.microsoft.com/v1.0/sites/{SHAREPOINT_SITE_ID}/lists/{list_id}/items?expand=fields&$top=100"
        items_resp = await client.get(items_url, headers=headers)
        items = items_resp.json().get("value", [])
        
        print(f"Total items fetched: {len(items)}")
        
        found = False
        for item in items:
            fields = item.get("fields", {})
            filename = fields.get("FileLeafRef", "")
            # Look for an item that has AOF or Icade or is one of the tagged files
            if "1Q2016" in filename or "presentation" in filename or "2025-half-year" in filename:
                print(f"\n--- MATCHED PDF: {filename} (ID: {item['id']}) ---")
                for k, v in fields.items():
                    print(f"{k}: {v}")
                found = True
        
        if not found:
            print("\nCould not find any matched PDF by name. Printing fields of the first 5 items instead:")
            for item in items[:5]:
                print(f"\n--- Item {item['id']} ({item.get('fields', {}).get('FileLeafRef')}) ---")
                for k, v in item.get("fields", {}).items():
                    print(f"{k}: {v}")

if __name__ == "__main__":
    asyncio.run(main())
