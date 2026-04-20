from app.api.routers.analysis_studio import router as analysis_studio_router
from app.api.routers.catalog import router as catalog_router
from app.api.routers.market_data import router as market_data_router
from app.api.routers.nba_read import router as nba_read_router
from app.api.routers.portfolio import router as portfolio_router
from app.api.routers.sync import router as sync_router
from app.api.routers.system_registry import router as system_registry_router

__all__ = [
    "analysis_studio_router",
    "catalog_router",
    "market_data_router",
    "nba_read_router",
    "portfolio_router",
    "sync_router",
    "system_registry_router",
]
