import os
import logging
import requests
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger(__name__)

class JinaSearchRequest(BaseModel):
    query: str
    limit: int = 5

class JinaSearchResult(BaseModel):
    title: str
    url: str
    description: str
    content: Optional[str] = None

class JinaSearchResponse(BaseModel):
    results: List[JinaSearchResult]

def fetch_jina_search(request: JinaSearchRequest) -> JinaSearchResponse:
    """
    Uses Jina Search API to find results for a query.
    """
    api_key = os.getenv("JINA_API_KEY")
    headers = {
        "Accept": "application/json"
    }
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    # Jina Search API
    jina_url = f"https://s.jina.ai/{request.query}"
    
    try:
        logger.info(f"Fetching Jina Search for: {request.query}")
        resp = requests.get(jina_url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        results = []
        # Jina Search response structure typically has a 'data' list
        if "data" in data:
            for item in data["data"]:
                results.append(JinaSearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    description=item.get("description", ""),
                    content=item.get("content") # Jina Search might return content snippet
                ))
        
        # Respect limit if possible (though Jina might not support limit param in URL directly in this form)
        # We slice the results locally.
        return JinaSearchResponse(results=results[:request.limit])
        
    except Exception as e:
        logger.error(f"Failed to fetch Jina Search for {request.query}: {e}")
        raise
