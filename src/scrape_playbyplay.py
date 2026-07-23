#!/usr/bin/env python3
"""
Play-by-Play Scraper
Scrapes play-by-play data from Basketball Reference and saves it in structured format.
"""

import sys
import json
import csv
import os
from datetime import datetime

try:
    import requests
except ImportError:
    print("Error: requests library is not installed.")
    print("Please install it using: pip install requests")
    sys.exit(1)

try:
    from bs4 import BeautifulSoup
except ImportError:
    print("Error: beautifulsoup4 library is not installed.")
    print("Please install it using: pip install beautifulsoup4")
    sys.exit(1)


def scrape_playbyplay(url):
    """
    Scrape play-by-play data from Basketball Reference.
    
    Args:
        url: Basketball Reference play-by-play URL
    
    Returns:
        List of play dictionaries
    """
    print(f"Fetching play-by-play data from: {url}")
    
    # Fetch the page
    response = requests.get(url)
    if response.status_code != 200:
        print(f"Error: Failed to fetch page (status code: {response.status_code})")
        sys.exit(1)
    
    # Parse HTML
    soup = BeautifulSoup(response.content, features="lxml")
    
    # Find the play-by-play table
    table = soup.find('table', {'class': 'suppress_all'})
    if not table:
        print("Error: Could not find play-by-play table")
        sys.exit(1)
    
    # Extract team names from page title (most reliable method)
    team1 = None
    team2 = None
    
    title = soup.find('title')
    if title:
        title_text = title.text.strip()
        # Format is usually "TeamA vs TeamB, Date" or "TeamA at TeamB Play-By-Play, Date"
        if ' vs ' in title_text:
            # Split on "vs"
            parts = title_text.split(' vs ')
            team1 = parts[0].strip()
            if len(parts) > 1:
                # Remove date info (everything after comma)
                team2_part = parts[1].split(',')[0].strip()
                team2 = team2_part
        elif ' at ' in title_text:
            # Fallback to "at" format
            parts = title_text.split(' at ')
            team1 = parts[0].strip()
            if len(parts) > 1:
                # Remove "Play-By-Play" and date info
                team2_part = parts[1].split(' Play-By-Play')[0].split(',')[0].strip()
                team2 = team2_part
    
    # Final fallback
    if team1 is None:
        team1 = "Team 1"
    if team2 is None:
        team2 = "Team 2"
    
    print(f"Teams: {team1} vs {team2}")
    
    plays = []
    current_quarter = None
    
    # Iterate through table rows
    for row in table.find_all('tr'):
        # Check if this is a quarter header
        th = row.find('th')
        if th and 'Q' in th.text:
            quarter_text = th.text.strip()
            if '1st Q' in quarter_text:
                current_quarter = 1
            elif '2nd Q' in quarter_text:
                current_quarter = 2
            elif '3rd Q' in quarter_text:
                current_quarter = 3
            elif '4th Q' in quarter_text:
                current_quarter = 4
            continue
        
        # Get all cells
        cells = row.find_all('td')
        
        # Skip if not enough cells or no quarter yet
        if len(cells) < 3 or current_quarter is None:
            continue
        
        # Find time (first td)
        time_text = cells[0].text.strip()
        
        # Skip if no time
        if not time_text:
            continue
        
        # Find the center td (has class 'center')
        center_idx = None
        for idx, cell in enumerate(cells):
            if 'center' in cell.get('class', []):
                center_idx = idx
                break
        
        # If no center found, skip
        if center_idx is None:
            continue
        
        # Score is in the center column
        score_text = cells[center_idx].text.strip()
        
        # Before center = team 1, after center = team 2
        team1_text = ""
        team2_text = ""
        
        # Get team 1 text (between time and center)
        for i in range(1, center_idx):
            text = cells[i].text.strip()
            # Skip score change indicators like "+2", "+3", "-2"
            if text and not (text.startswith('+') or text.startswith('-')):
                team1_text = text
                break
        
        # Get team 2 text (after center)
        for i in range(center_idx + 1, len(cells)):
            text = cells[i].text.strip()
            # Skip score change indicators like "+2", "+3", "-2"
            if text and not (text.startswith('+') or text.startswith('-')):
                team2_text = text
                break
        
        # Determine which team made the play
        if team1_text and not team2_text:
            team = team1
            description = team1_text
        elif team2_text and not team1_text:
            team = team2
            description = team2_text
        elif team1_text and team2_text:
            # Both teams have text (rare)
            team = "Both"
            description = f"{team1}: {team1_text} | {team2}: {team2_text}"
        else:
            # Neither has text, skip
            continue
        
        # Create play dictionary
        play = {
            'quarter': current_quarter,
            'time': time_text,
            'score': score_text,
            'team': team,
            'description': description
        }
        
        plays.append(play)
    
    print(f"✓ Scraped {len(plays)} plays")
    return plays


def save_to_json(plays, output_path):
    """Save plays to JSON file."""
    with open(output_path, 'w') as f:
        json.dump(plays, f, indent=2)
    print(f"✓ Saved to JSON: {output_path}")


def save_to_csv(plays, output_path):
    """Save plays to CSV file."""
    if not plays:
        print("No plays to save")
        return
    
    with open(output_path, 'w', newline='') as f:
        fieldnames = ['quarter', 'time', 'score', 'team', 'description']
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        
        writer.writeheader()
        for play in plays:
            writer.writerow(play)
    
    print(f"✓ Saved to CSV: {output_path}")


def main():
    """Main function."""
    if len(sys.argv) < 2:
        print("Usage: python scrape_playbyplay.py <basketball-reference-url> [--format json|csv|both]")
        print("Example: python scrape_playbyplay.py https://www.basketball-reference.com/boxscores/pbp/202511110PHI.html")
        sys.exit(1)
    
    url = sys.argv[1]
    output_format = 'both'  # Default to both
    
    if len(sys.argv) > 2 and sys.argv[2] == '--format':
        if len(sys.argv) > 3:
            output_format = sys.argv[3]
    
    # Scrape data
    plays = scrape_playbyplay(url)
    
    # Create output directory
    os.makedirs('playbyplay', exist_ok=True)
    
    # Extract game identifier from URL (e.g., 202511110PHI)
    game_id = url.split('/')[-1].replace('.html', '')
    
    # Generate output filenames
    json_path = f'playbyplay/{game_id}.json'
    csv_path = f'playbyplay/{game_id}.csv'
    
    # Save in requested format(s)
    if output_format in ['json', 'both']:
        save_to_json(plays, json_path)
    
    if output_format in ['csv', 'both']:
        save_to_csv(plays, csv_path)
    
    # Print summary
    print("\n" + "="*70)
    print("SCRAPING COMPLETE")
    print("="*70)
    print(f"Total plays: {len(plays)}")
    print(f"Quarters: {max(p['quarter'] for p in plays) if plays else 0}")
    print(f"Game ID: {game_id}")
    print("="*70)


if __name__ == "__main__":
    main()

