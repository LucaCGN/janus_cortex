
import requests
import re
from bs4 import BeautifulSoup
import time

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def discover_ids():
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    mapping = {}
    
    print("Scanning IDs 1 to 32...")
    for team_id in range(1, 33): # 30 teams, maybe strict 1-30?
        # Try finding the team name. Use 'x' as slug.
        url = f"https://www.hoopsstats.com/basketball/fantasy/nba/x/team/profile/26/{team_id}"
        
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                
                # Title often contains team name: "HoopsStats.com - NBA Fantasy Basketball - Oklahoma City Thunder Team Profile"
                title = soup.title.get_text() if soup.title else ""
                
                # Check known patterns
                if "Team Profile" in title:
                    # Parse "X Team Profile"
                    # "HoopsStats.com - NBA Fantasy Basketball - Atlanta Hawks Team Profile"
                    parts = title.split("-")
                    if len(parts) >= 3:
                        name_part = parts[-1].strip() # "Atlanta Hawks Team Profile"
                        team_name = name_part.replace(" Team Profile", "").strip()
                        mapping[team_id] = team_name
                        print(f"Found: {team_id} -> {team_name}")
                else:
                    print(f"ID {team_id}: Page found but title mismatch? '{title}'")
            else:
                print(f"ID {team_id}: Status {resp.status_code}")
                
        except Exception as e:
            print(f"ID {team_id}: Error {e}")
            
        time.sleep(1) # Be nice
        
    print("\nMapping Result:")
    print(mapping)

if __name__ == "__main__":
    discover_ids()
