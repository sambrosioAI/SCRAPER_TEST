import httpx
import os
import asyncio
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load env file from parent directory
load_dotenv("../.env")

TENANT_ID = os.getenv("TENANT_ID")
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
SHAREPOINT_SITE_URL = os.getenv("SHAREPOINT_SITE_URL")

async def get_sp_token():
    url = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "https://inmobcolonial.sharepoint.com/.default",
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return resp.json()["access_token"]

async def main():
    token = await get_sp_token()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json;odata=verbose"
    }
    
    parsed = urlparse(SHAREPOINT_SITE_URL)
    site_url = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
    
    async with httpx.AsyncClient() as client:
        # Test 1: Web endpoint
        web_url = f"{site_url}/_api/web"
        print(f"Testing GET: {web_url}")
        resp = await client.get(web_url, headers=headers)
        print(f"Web status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Web error details: {resp.text}")
            
        # Test 2: Lists endpoint
        lists_url = f"{site_url}/_api/web/lists"
        print(f"Testing GET: {lists_url}")
        resp = await client.get(lists_url, headers=headers)
        print(f"Lists status: {resp.status_code}")
        if resp.status_code == 200:
            lists = resp.json().get("d", {}).get("results", [])
            for l in lists:
                print(f"List Title: {l.get('Title')} | ID: {l.get('Id')}")
        else:
            print(f"Lists error details: {resp.text}")

if __name__ == "__main__":
    asyncio.run(main())
