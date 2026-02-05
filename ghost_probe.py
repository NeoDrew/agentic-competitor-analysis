#!/usr/bin/env python3
"""
Ghost Probe - ATS Detection and Job Listing Scraper
Detects Greenhouse, Lever, and Ashby ATS systems and extracts job listings.
"""
import argparse
import re
import json
from collections import Counter
import requests
from bs4 import BeautifulSoup

# Common headers to avoid bot detection
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# ATS patterns to detect
ATS_PATTERNS = {
    'greenhouse': [
        r'job-boards\.greenhouse\.io/[\w-]+',
        r'boards\.greenhouse\.io/[\w-]+',
    ],
    'lever': [
        r'jobs\.lever\.co/[\w-]+',
    ],
    'ashby': [
        r'jobs\.ashbyhq\.com/[\w-]+',
    ],
}


def detect_ats(url: str) -> dict | None:
    """
    Scrapes a company's careers page to find ATS links.

    Returns:
        dict with 'type' (greenhouse/lever/ashby) and 'url', or None if not found.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return None

    html = resp.text
    soup = BeautifulSoup(html, 'html.parser')

    # Search in href attributes and raw HTML
    all_links = set()

    # Extract all href values
    for tag in soup.find_all(['a', 'iframe', 'script']):
        href = tag.get('href') or tag.get('src') or ''
        all_links.add(href)

    # Also search raw HTML for embedded URLs
    all_text = html

    # Check each ATS pattern
    for ats_type, patterns in ATS_PATTERNS.items():
        for pattern in patterns:
            # Search in extracted links
            for link in all_links:
                if re.search(pattern, link, re.IGNORECASE):
                    ats_url = link if link.startswith(
                        'http') else f'https://{link}'
                    return {'type': ats_type, 'url': ats_url}

            # Search in raw HTML
            match = re.search(f'https?://{pattern}', all_text, re.IGNORECASE)
            if match:
                return {'type': ats_type, 'url': match.group(0)}

    # Check for embedded iframes that might contain ATS
    for iframe in soup.find_all('iframe'):
        src = iframe.get('src', '')
        for ats_type, patterns in ATS_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, src, re.IGNORECASE):
                    return {'type': ats_type, 'url': src}

    return None


def fetch_jobs(ats_url: str, ats_type: str = None) -> list[dict]:
    """
    Fetches job listings from a detected ATS URL.

    Args:
        ats_url: The ATS board URL
        ats_type: Optional type hint (greenhouse/lever/ashby)

    Returns:
        List of job dicts with title, department, location
    """
    # Auto-detect type if not provided
    if not ats_type:
        if 'greenhouse' in ats_url:
            ats_type = 'greenhouse'
        elif 'lever' in ats_url:
            ats_type = 'lever'
        elif 'ashby' in ats_url:
            ats_type = 'ashby'
        else:
            print(f"Unknown ATS type for URL: {ats_url}")
            return []

    try:
        resp = requests.get(ats_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"Error fetching jobs from {ats_url}: {e}")
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')

    if ats_type == 'greenhouse':
        return _parse_greenhouse(soup)
    elif ats_type == 'lever':
        return _parse_lever(soup)
    elif ats_type == 'ashby':
        # Try Ashby API first, then fall back to HTML parsing
        jobs = _fetch_ashby_api(ats_url)
        if jobs:
            return jobs
        return _parse_ashby(soup)

    return []


def _fetch_ashby_api(ats_url: str) -> list[dict]:
    """Fetch jobs from Ashby GraphQL API."""
    # Extract company slug from URL (e.g., https://jobs.ashbyhq.com/linear -> linear)
    match = re.search(r'jobs\.ashbyhq\.com/([^/?]+)', ats_url)
    if not match:
        return []

    company_slug = match.group(1)
    api_url = f'https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams'

    # GraphQL query to fetch job postings
    payload = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {
            "organizationHostedJobsPageName": company_slug
        },
        "query": """
            query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
                jobBoard: jobBoardWithTeams(
                    organizationHostedJobsPageName: $organizationHostedJobsPageName
                ) {
                    jobPostings {
                        id
                        title
                        teamId
                        locationId
                        locationName
                        employmentType
                        secondaryLocations {
                            locationId
                            locationName
                        }
                    }
                    teams {
                        id
                        name
                        parentTeamId
                    }
                }
            }
        """
    }

    try:
        resp = requests.post(api_url, json=payload, headers={
            **HEADERS,
            'Content-Type': 'application/json',
        }, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        job_board = data.get('data', {}).get('jobBoard')
        if not job_board:
            # Company doesn't exist on Ashby
            return []

        job_postings = job_board.get('jobPostings') or []
        teams_list = job_board.get('teams') or []
        # Clean team names - remove numeric prefixes like "32010 "
        teams = {}
        for t in teams_list:
            if t.get('id') and t.get('name'):
                name = t['name']
                # Remove leading numeric IDs (e.g., "32010 Backend Engineering" -> "Backend Engineering")
                name = re.sub(r'^\d+\s+', '', name)
                teams[t['id']] = name

        jobs = []
        for posting in job_postings:
            job = {
                'title': posting.get('title', ''),
                'location': posting.get('locationName', 'Not specified'),
                'department': teams.get(posting.get('teamId'), 'General'),
            }
            if job['title']:
                jobs.append(job)

        return jobs
    except (requests.RequestException, json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Ashby API request failed: {e}")
        return []


def _parse_greenhouse(soup: BeautifulSoup) -> list[dict]:
    """Parse Greenhouse job board HTML."""
    jobs = []

    # Method 1: Classic Greenhouse structure (div class="opening")
    for opening in soup.find_all('div', class_='opening'):
        job = {}
        title_link = opening.find('a')
        if title_link:
            job['title'] = title_link.get_text(strip=True)

        location = opening.find('span', class_='location')
        job['location'] = location.get_text(strip=True) if location else 'Not specified'

        parent_section = opening.find_parent('section')
        if parent_section:
            dept_header = parent_section.find(['h2', 'h3', 'h4'])
            job['department'] = dept_header.get_text(strip=True) if dept_header else 'General'
        else:
            job['department'] = 'General'

        if job.get('title'):
            jobs.append(job)

    if jobs:
        return jobs

    # Method 2: New job-boards.greenhouse.io structure
    # Jobs are links to /jobs/{id} grouped under department headings
    # Filter out navigation/category links
    skip_titles = {
        'apply', 'view', 'see all', 'all open positions', 'see all open positions',
        'view all', 'learn more', 'read more', 'careers', 'jobs', 'home',
        'about', 'benefits', 'culture', 'teams', 'locations'
    }

    current_dept = 'General'
    for element in soup.find_all(['h2', 'h3', 'h4', 'a']):
        if element.name in ['h2', 'h3', 'h4']:
            # Department header
            current_dept = element.get_text(strip=True)
        elif element.name == 'a':
            href = element.get('href', '')
            # Job links contain /jobs/{numeric_id} in the path
            if re.search(r'/jobs/\d+', href):
                title = element.get_text(strip=True)
                title_lower = title.lower()

                # Skip navigation links and categories
                if len(title) < 10:
                    continue
                if title_lower in skip_titles:
                    continue
                if any(skip in title_lower for skip in skip_titles):
                    continue
                # Skip if it looks like an office location (short name, no role keywords)
                role_keywords = ['engineer', 'manager', 'director', 'analyst', 'designer',
                                 'developer', 'lead', 'head', 'specialist', 'coordinator']
                if len(title.split()) <= 3 and not any(kw in title_lower for kw in role_keywords):
                    continue

                job = {
                    'title': title,
                    'department': current_dept,
                    'location': 'Not specified'
                }
                # Try to find location near the link
                next_sibling = element.find_next_sibling()
                if next_sibling and next_sibling.get_text(strip=True):
                    loc_text = next_sibling.get_text(strip=True)
                    if len(loc_text) < 100:  # Likely location, not description
                        job['location'] = loc_text
                jobs.append(job)

    if jobs:
        return jobs

    # Method 3: Generic fallback - divs with job-related classes
    for posting in soup.find_all('div', class_=re.compile(r'job|posting|position', re.I)):
        job = {}
        title = posting.find(['a', 'h3', 'h4'], class_=re.compile(r'title|name', re.I))
        if title:
            job['title'] = title.get_text(strip=True)
        location = posting.find(class_=re.compile(r'location', re.I))
        job['location'] = location.get_text(strip=True) if location else 'Not specified'
        dept = posting.find(class_=re.compile(r'department|team', re.I))
        job['department'] = dept.get_text(strip=True) if dept else 'General'
        if job.get('title'):
            jobs.append(job)

    return jobs


def _parse_lever(soup: BeautifulSoup) -> list[dict]:
    """Parse Lever job board HTML."""
    jobs = []

    # Lever uses <div class="posting"> for each job
    for posting in soup.find_all('div', class_='posting'):
        job = {}

        # Title in <a class="posting-title">
        title = posting.find(['a', 'h5'], class_=re.compile(
            r'posting-title|title', re.I))
        if title:
            job['title'] = title.get_text(strip=True)

        # Location in <span class="sort-by-location">
        location = posting.find('span', class_=re.compile(
            r'location|workplaceType', re.I))
        if location:
            job['location'] = location.get_text(strip=True)
        else:
            # Try finding in posting-categories
            categories = posting.find('div', class_='posting-categories')
            if categories:
                loc_span = categories.find('span', class_='sort-by-location')
                job['location'] = loc_span.get_text(
                    strip=True) if loc_span else 'Not specified'
            else:
                job['location'] = 'Not specified'

        # Department/Team in <span class="sort-by-team">
        team = posting.find(
            'span', class_=re.compile(r'team|department', re.I))
        if team:
            job['department'] = team.get_text(strip=True)
        else:
            job['department'] = 'General'

        if job.get('title'):
            jobs.append(job)

    # Lever also groups by department with <div class="posting-group">
    if not jobs:
        for group in soup.find_all('div', class_='posting-group'):
            dept_header = group.find('div', class_='posting-group-header')
            department = dept_header.get_text(
                strip=True) if dept_header else 'General'

            for posting in group.find_all('a', class_='posting-title'):
                job = {
                    'title': posting.get_text(strip=True),
                    'department': department,
                    'location': 'Not specified'
                }
                jobs.append(job)

    return jobs


def _parse_ashby(soup: BeautifulSoup) -> list[dict]:
    """Parse Ashby job board - handles JSON data embedded in page."""
    jobs = []
    print("Parsing Ashby job board...")

    # Ashby embeds job data as JSON in script tags (Next.js __NEXT_DATA__ or inline scripts)
    # First, try to find __NEXT_DATA__ script tag
    next_data_script = soup.find('script', id='__NEXT_DATA__')
    if next_data_script:
        try:
            data = json.loads(next_data_script.string)
            # Navigate to job postings in Next.js data structure
            props = data.get('props', {}).get('pageProps', {})
            job_postings = props.get('jobPostings', []) or props.get('jobs', [])
            for posting in job_postings:
                job = {
                    'title': posting.get('title', ''),
                    'location': posting.get('location', {}).get('name', 'Not specified') if isinstance(posting.get('location'), dict) else posting.get('locationName', 'Not specified'),
                    'department': posting.get('team', {}).get('name', 'General') if isinstance(posting.get('team'), dict) else posting.get('departmentName', 'General'),
                }
                if job['title']:
                    jobs.append(job)
            if jobs:
                return jobs
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"Failed to parse __NEXT_DATA__: {e}")

    # Try to find any script tag containing job posting JSON
    for script in soup.find_all('script'):
        if script.string and 'jobPosting' in script.string:
            try:
                # Try to extract JSON from the script content
                match = re.search(r'\{.*"jobPosting.*\}', script.string, re.DOTALL)
                if match:
                    data = json.loads(match.group(0))
                    # Process the data
                    job_postings = data.get('jobPostings', []) or data.get('jobs', [])
                    for posting in job_postings:
                        job = {
                            'title': posting.get('title', ''),
                            'location': posting.get('location', {}).get('name', 'Not specified') if isinstance(posting.get('location'), dict) else 'Not specified',
                            'department': posting.get('team', {}).get('name', 'General') if isinstance(posting.get('team'), dict) else 'General',
                        }
                        if job['title']:
                            jobs.append(job)
                    if jobs:
                        return jobs
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    # Try parsing the raw text as JSON (in case the response is pure JSON)
    raw_text = soup.get_text()
    try:
        # Look for JSON array of job postings
        if raw_text.strip().startswith('[') or raw_text.strip().startswith('{'):
            data = json.loads(raw_text.strip())
            # Handle both array and object responses
            if isinstance(data, dict):
                job_postings = data.get('jobPostings', []) or data.get('jobs', []) or data.get('results', [])
            else:
                job_postings = data

            for posting in job_postings:
                if isinstance(posting, dict):
                    job = {
                        'title': posting.get('title', ''),
                        'location': posting.get('location', {}).get('name', 'Not specified') if isinstance(posting.get('location'), dict) else posting.get('locationName', 'Not specified'),
                        'department': posting.get('team', {}).get('name', 'General') if isinstance(posting.get('team'), dict) else posting.get('departmentName', 'General'),
                    }
                    if job['title']:
                        jobs.append(job)
            if jobs:
                return jobs
    except (json.JSONDecodeError, TypeError):
        pass

    # Fallback: try HTML parsing for older Ashby boards
    for posting in soup.find_all(['div', 'a'], class_=re.compile(r'job|posting|position|opening', re.I)):
        job = {}
        title = posting.find(['h3', 'h4', 'a', 'span'], class_=re.compile(r'title|name', re.I))
        if not title:
            title = posting.find(['h3', 'h4'])
        if title:
            job['title'] = title.get_text(strip=True)
        location = posting.find(class_=re.compile(r'location', re.I))
        job['location'] = location.get_text(strip=True) if location else 'Not specified'
        dept = posting.find(class_=re.compile(r'department|team', re.I))
        job['department'] = dept.get_text(strip=True) if dept else 'General'
        if job.get('title') and len(job['title']) > 2:
            jobs.append(job)

    # Deduplicate
    seen = set()
    unique_jobs = []
    for job in jobs:
        key = (job['title'], job.get('location', ''))
        if key not in seen:
            seen.add(key)
            unique_jobs.append(job)

    return unique_jobs


def analyze_hiring_trends(old_jobs: list[dict], new_jobs: list[dict]) -> dict:
    """
    Analyzes changes between two job listing snapshots.

    Returns:
        Dict with velocity change, keyword analysis, and specific findings.
    """
    old_count = len(old_jobs)
    new_count = len(new_jobs)

    # Calculate velocity change
    if old_count > 0:
        velocity_change = ((new_count - old_count) / old_count) * 100
    else:
        velocity_change = 100 if new_count > 0 else 0

    # Track keywords of interest
    keywords = ['AI', 'ML', 'Machine Learning', 'Enterprise', 'Sales', 'Security',
                'Platform', 'Infrastructure', 'Staff', 'Principal', 'Director', 'VP']

    old_titles = [j['title'].lower() for j in old_jobs]
    new_titles = [j['title'].lower() for j in new_jobs]

    keyword_changes = {}
    for kw in keywords:
        kw_lower = kw.lower()
        old_hits = sum(1 for t in old_titles if kw_lower in t)
        new_hits = sum(1 for t in new_titles if kw_lower in t)
        if old_hits != new_hits:
            keyword_changes[kw] = {'old': old_hits,
                                   'new': new_hits, 'delta': new_hits - old_hits}

    # Find new and removed roles
    old_title_set = set(old_titles)
    new_title_set = set(new_titles)

    new_roles = [j for j in new_jobs if j['title'].lower()
                 not in old_title_set]
    removed_roles = [j for j in old_jobs if j['title'].lower()
                     not in new_title_set]

    # Department breakdown
    old_depts = Counter(j.get('department', 'General') for j in old_jobs)
    new_depts = Counter(j.get('department', 'General') for j in new_jobs)

    dept_changes = {}
    all_depts = set(old_depts.keys()) | set(new_depts.keys())
    for dept in all_depts:
        old_c = old_depts.get(dept, 0)
        new_c = new_depts.get(dept, 0)
        if old_c != new_c:
            dept_changes[dept] = {'old': old_c,
                                  'new': new_c, 'delta': new_c - old_c}

    # Generate summary
    if velocity_change > 0:
        velocity_summary = f"Hiring velocity increased by {velocity_change:.0f}%"
    elif velocity_change < 0:
        velocity_summary = f"Hiring velocity decreased by {abs(velocity_change):.0f}%"
    else:
        velocity_summary = "Hiring velocity unchanged"

    return {
        'summary': velocity_summary,
        'old_count': old_count,
        'new_count': new_count,
        'velocity_change_percent': round(velocity_change, 1),
        'keyword_changes': keyword_changes,
        'department_changes': dept_changes,
        'new_roles': new_roles[:10],  # Limit to top 10
        'removed_roles': removed_roles[:10],
    }


def print_jobs(jobs: list[dict], title: str = "Open Roles"):
    """Pretty print job listings."""
    print(f"\n{'='*60}")
    print(f" {title} ({len(jobs)} positions)")
    print('='*60)

    # Group by department
    by_dept = {}
    for job in jobs:
        dept = job.get('department', 'General')
        if dept not in by_dept:
            by_dept[dept] = []
        by_dept[dept].append(job)

    for dept, dept_jobs in sorted(by_dept.items()):
        print(f"\n[{dept}]")
        for job in dept_jobs:
            location = job.get('location', 'Not specified')
            print(f"  - {job['title']}")
            print(f"    Location: {location}")


def print_analysis(analysis: dict):
    """Pretty print hiring trend analysis."""
    print(f"\n{'='*60}")
    print(" HIRING TREND ANALYSIS")
    print('='*60)

    print(f"\n{analysis['summary']}")
    print(f"  Previous: {analysis['old_count']} roles")
    print(f"  Current:  {analysis['new_count']} roles")

    if analysis['keyword_changes']:
        print("\nKeyword Changes:")
        for kw, data in analysis['keyword_changes'].items():
            direction = "+" if data['delta'] > 0 else ""
            print(
                f"  {kw}: {data['old']} -> {data['new']} ({direction}{data['delta']})")

    if analysis['department_changes']:
        print("\nDepartment Changes:")
        for dept, data in analysis['department_changes'].items():
            direction = "+" if data['delta'] > 0 else ""
            print(
                f"  {dept}: {data['old']} -> {data['new']} ({direction}{data['delta']})")

    if analysis['new_roles']:
        print(f"\nNew Roles Added ({len(analysis['new_roles'])}):")
        for role in analysis['new_roles'][:5]:
            print(f"  + {role['title']}")

    if analysis['removed_roles']:
        print(f"\nRoles Removed ({len(analysis['removed_roles'])}):")
        for role in analysis['removed_roles'][:5]:
            print(f"  - {role['title']}")


def main():
    parser = argparse.ArgumentParser(
        description="Ghost Probe - Detect ATS and scrape job listings"
    )
    parser.add_argument(
        "url", help="Company careers page URL (e.g., https://linear.app/careers)")
    parser.add_argument("--ats-url", help="Direct ATS URL (skip detection)")
    parser.add_argument("--ats-type", choices=['greenhouse', 'lever', 'ashby'],
                        help="ATS type (if providing direct URL)")
    parser.add_argument(
        "--output", "-o", help="Output JSON file for job listings")
    parser.add_argument(
        "--compare", help="Compare with previous JSON snapshot")

    args = parser.parse_args()

    # Step 1: Detect or use provided ATS
    if args.ats_url:
        ats = {'type': args.ats_type, 'url': args.ats_url}
        print(f"Using provided ATS URL: {args.ats_url}")
    else:
        print(f"Scanning {args.url} for ATS...")
        ats = detect_ats(args.url)

    if not ats:
        print("No supported ATS detected (Greenhouse, Lever, Ashby).")
        print("Try providing the ATS URL directly with --ats-url")
        return

    print(f"Detected ATS: {ats['type'].upper()}")
    print(f"Board URL: {ats['url']}")

    # Step 2: Fetch job listings
    print("\nFetching job listings...")
    jobs = fetch_jobs(ats['url'], ats['type'])

    if not jobs:
        print("No jobs found. The page structure may have changed.")
        return

    # Step 3: Display results
    print_jobs(jobs)

    # Step 4: Save to JSON if requested
    if args.output:
        output_data = {
            'url': args.url,
            'ats': ats,
            'jobs': jobs,
            'count': len(jobs)
        }
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2)
        print(f"\nSaved {len(jobs)} jobs to {args.output}")

    # Step 5: Compare with previous snapshot if provided
    if args.compare:
        try:
            with open(args.compare) as f:
                old_data = json.load(f)
            old_jobs = old_data.get('jobs', [])
            analysis = analyze_hiring_trends(old_jobs, jobs)
            print_analysis(analysis)
        except FileNotFoundError:
            print(f"Comparison file not found: {args.compare}")
        except json.JSONDecodeError:
            print(f"Invalid JSON in comparison file: {args.compare}")


if __name__ == "__main__":
    main()
