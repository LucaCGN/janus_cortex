"""HoopsStats source connectors."""

from app.data.nodes.hoopsstats.matchups_scraper_beautiful_soup import scrape_data as scrape_matchups_data
from app.data.nodes.hoopsstats.profile_scraper_beautiful_soup import scrape_data as scrape_profile_data
from app.data.nodes.hoopsstats.quarters_scraper_beautiful_soup import scrape_data as scrape_quarters_data
from app.data.nodes.hoopsstats.streaks_scraper_beautiful_soup import scrape_data as scrape_streaks_data
from app.data.nodes.hoopsstats.tips_scraper_beautiful_soup import scrape_data as scrape_tips_data

__all__ = [
    "scrape_matchups_data",
    "scrape_profile_data",
    "scrape_quarters_data",
    "scrape_streaks_data",
    "scrape_tips_data",
]
