"""JinaAI source wrappers."""

from app.data.nodes.jinaai.reader import fetch_jina_reader
from app.data.nodes.jinaai.search import fetch_jina_search

__all__ = [
    "fetch_jina_reader",
    "fetch_jina_search",
]
