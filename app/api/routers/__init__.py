from app.api.routers.catalog import router as catalog_router
from app.api.routers.nba_read import router as nba_read_router
from app.api.routers.sync import router as sync_router
from app.api.routers.system_registry import router as system_registry_router

__all__ = [
    "catalog_router",
    "nba_read_router",
    "sync_router",
    "system_registry_router",
]
