# Jina AI Node

**Path**: `app/data/nodes/jinaai`

## Overview
This node integrates with Jina AI APIs to search the web and read/parse URL content into Markdown for LLM consumption.

## Components

### 1. Reader (`reader.py`)
- **Function**: `fetch_jina_reader(url)`
- **Input**: URL string.
- **Output**: `JinaReaderResponse` (title, content in markdown).
- **Usage**: Extracting text from news, articles, or stats pages.

### 2. Search (`search.py`)
- **Function**: `fetch_jina_search(query)`
- **Input**: Search query string.
- **Output**: List of results (title, url, snippet).
- **Usage**: Real-time research (e.g., "NBA injury report today").

## Database State
- **Stateless**. This node does not persist data to the local DB directly; it returns data for immediate use by agents or pipelines.

## Verification
- **Test Script**: `dev/tests/validate_jina.py`
- **Result**: **SUCCESS** (Mocked). Code logic is valid.
