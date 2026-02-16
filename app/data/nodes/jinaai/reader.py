import os
import logging
import requests
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)

class JinaReaderRequest(BaseModel):
    url: str

class JinaReaderResponse(BaseModel):
    url: str
    content: str
    title: Optional[str] = None
    description: Optional[str] = None

def fetch_jina_reader(request: JinaReaderRequest) -> JinaReaderResponse:
    """
    Uses Jina Reader API to convert a URL to Markdown.
    """
    api_key = os.getenv("JINA_API_KEY")
    headers = {
        "Accept": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    jina_url = f"https://r.jina.ai/{request.url}"
    
    try:
        logger.info(f"Fetching Jina Reader for: {request.url}")
        resp = requests.get(jina_url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        return JinaReaderResponse(
            url=data.get("url", request.url),
            content=data.get("content", ""),
            title=data.get("title"),
            description=data.get("description")
        )
    except Exception as e:
        logger.error(f"Failed to fetch Jina Reader for {request.url}: {e}")
        # Return empty response on failure to avoid breaking pipelines? 
        # Or raise? Let's raise for now so caller knows.
        raise
