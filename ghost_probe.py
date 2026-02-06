#!/usr/bin/env python3
"""
Ghost Probe - ATS Detection and Job Listing Scraper
Detects Greenhouse, Lever, and Ashby ATS systems and extracts job listings.
Also supports levels.fyi as a fallback data source for companies without standard ATS.
"""
import argparse
import re
import json
import os
from collections import Counter
import requests
from bs4 import BeautifulSoup

# Optional: Gemini for AI-powered job extraction
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

# Common headers to avoid bot detection
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}

# Levels.fyi slug mappings for companies with non-obvious slugs
# Maps product names to their parent company's levels.fyi slug
LEVELSFYI_SLUGS = {
    # Monday.com
    'monday.com': 'mondaycom',
    'monday': 'mondaycom',
    # Atlassian products
    'atlassian': 'atlassian',
    'jira': 'atlassian',
    'confluence': 'atlassian',
    'trello': 'atlassian',
    'bitbucket': 'atlassian',
    # Microsoft products
    'microsoft': 'microsoft',
    'azure': 'microsoft',
    'azure devops': 'microsoft',
    'azuredevops': 'microsoft',
    'teams': 'microsoft',
    # GitHub (separate from Microsoft on levels.fyi)
    'github': 'github',
    'github projects': 'github',
    'githubprojects': 'github',
    # GitLab
    'gitlab': 'gitlab',
    # Other companies
    'notion': 'notion',
    'figma': 'figma',
    'stripe': 'stripe',
    'airbnb': 'airbnb',
    'asana': 'asana',
    'clickup': 'clickup',
    'linear': 'linear',
    'slack': 'slack',
    'zoom': 'zoom',
    'salesforce': 'salesforce',
    'google': 'google',
    'meta': 'meta',
    'facebook': 'meta',
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
    Uses official APIs where available (Greenhouse, Lever, Ashby) for complete results.

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

    # Try official APIs first (returns ALL jobs), fall back to HTML parsing
    if ats_type == 'greenhouse':
        jobs = _fetch_greenhouse_api(ats_url)
        if jobs:
            return jobs
        # Fallback to HTML parsing
        print("  Greenhouse API failed, falling back to HTML parsing...")

    elif ats_type == 'lever':
        jobs = _fetch_lever_api(ats_url)
        if jobs:
            return jobs
        # Fallback to HTML parsing
        print("  Lever API failed, falling back to HTML parsing...")

    elif ats_type == 'ashby':
        jobs = _fetch_ashby_api(ats_url)
        if jobs:
            return jobs
        # Fallback to HTML parsing
        print("  Ashby API failed, falling back to HTML parsing...")

    # HTML parsing fallback
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
        return _parse_ashby(soup)

    return []


def _fetch_greenhouse_api(ats_url: str) -> list[dict]:
    """
    Fetch ALL jobs from Greenhouse using their public Job Board API.
    This API returns all jobs in a single request - no pagination needed.

    API docs: https://developers.greenhouse.io/job-board.html
    """
    # Extract board token from URL
    # Patterns: job-boards.greenhouse.io/{token} or boards.greenhouse.io/{token}
    match = re.search(r'(?:job-boards|boards)\.greenhouse\.io/([^/?]+)', ats_url)
    if not match:
        print(f"  Could not extract Greenhouse board token from: {ats_url}")
        return []

    board_token = match.group(1)
    api_url = f'https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs'

    print(f"  Fetching from Greenhouse API: {api_url}")

    try:
        # Add content=true to get department info
        resp = requests.get(
            api_url,
            params={'content': 'true'},
            headers={'Accept': 'application/json'},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()

        jobs_data = data.get('jobs', [])
        total = data.get('meta', {}).get('total', len(jobs_data))
        print(f"  ✓ Greenhouse API returned {total} jobs")

        jobs = []
        for job in jobs_data:
            # Extract department from departments array
            departments = job.get('departments', [])
            department = departments[0].get('name', 'General') if departments else 'General'

            # Extract location
            location = job.get('location', {})
            location_name = location.get('name', 'Not specified') if isinstance(location, dict) else 'Not specified'

            jobs.append({
                'title': job.get('title', ''),
                'department': department,
                'location': location_name,
            })

        return jobs

    except requests.RequestException as e:
        print(f"  ✗ Greenhouse API request failed: {e}")
        return []
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"  ✗ Greenhouse API parse error: {e}")
        return []


def _fetch_lever_api(ats_url: str) -> list[dict]:
    """
    Fetch ALL jobs from Lever using their public Postings API with pagination.
    Iterates through all pages to get complete job list.

    API docs: https://github.com/lever/postings-api
    """
    # Extract company slug from URL (e.g., jobs.lever.co/company -> company)
    match = re.search(r'jobs\.lever\.co/([^/?]+)', ats_url)
    if not match:
        print(f"  Could not extract Lever company slug from: {ats_url}")
        return []

    company_slug = match.group(1)

    # Determine if EU or global API
    if '.eu.' in ats_url:
        api_base = f'https://api.eu.lever.co/v0/postings/{company_slug}'
    else:
        api_base = f'https://api.lever.co/v0/postings/{company_slug}'

    print(f"  Fetching from Lever API: {api_base}")

    all_jobs = []
    offset = 0
    page_size = 100  # Max allowed by Lever API

    try:
        while True:
            resp = requests.get(
                api_base,
                params={
                    'mode': 'json',
                    'skip': offset,
                    'limit': page_size
                },
                headers={'Accept': 'application/json'},
                timeout=30
            )
            resp.raise_for_status()
            page_jobs = resp.json()

            if not page_jobs:
                break

            for job in page_jobs:
                categories = job.get('categories', {})

                # Get department/team
                team = categories.get('team', '')
                department = categories.get('department', '')
                dept_name = team or department or 'General'

                # Get location
                locations = categories.get('location', [])
                location = locations[0] if locations else 'Not specified'
                if isinstance(location, dict):
                    location = location.get('name', 'Not specified')

                all_jobs.append({
                    'title': job.get('text', ''),
                    'department': dept_name,
                    'location': location,
                })

            print(f"    Page {offset // page_size + 1}: {len(page_jobs)} jobs")

            if len(page_jobs) < page_size:
                # Last page
                break

            offset += page_size

        print(f"  ✓ Lever API returned {len(all_jobs)} total jobs")
        return all_jobs

    except requests.RequestException as e:
        print(f"  ✗ Lever API request failed: {e}")
        return []
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"  ✗ Lever API parse error: {e}")
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


def fetch_jobs_from_levelsfyi(company_slug: str, max_pages: int = 10) -> list[dict]:
    """
    Fetch job listings from levels.fyi for companies without standard ATS.

    Note: levels.fyi currently limits results to 15 jobs per company in their
    public interface. This provides a sample of the most relevant current
    openings, which is useful for competitive intelligence even if not exhaustive.

    Args:
        company_slug: The company identifier on levels.fyi (e.g., 'mondaycom', 'atlassian')
        max_pages: Maximum number of pages to fetch (largely unused due to 15-job limit)

    Returns:
        List of job dicts with title, department, location (up to 15 jobs)
    """
    # Normalize slug
    company_slug = company_slug.lower().replace(' ', '').replace('.', '').replace('-', '')
    if company_slug in LEVELSFYI_SLUGS:
        company_slug = LEVELSFYI_SLUGS[company_slug]

    base_url = f"https://www.levels.fyi/jobs/company/{company_slug}"
    all_jobs = []
    seen_titles = set()

    print(f"  Fetching jobs from levels.fyi/{company_slug}...")

    # Fetch first page to get total count
    try:
        resp = requests.get(base_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  ✗ levels.fyi returned {resp.status_code}")
            return []

        # Extract jobs from first page
        page_jobs = _parse_levelsfyi_page(resp.text)
        for job in page_jobs:
            title_key = (job['title'], job.get('location', ''))
            if title_key not in seen_titles:
                seen_titles.add(title_key)
                all_jobs.append(job)

        print(f"    Page 1: {len(page_jobs)} jobs")

        # Try to find total job count from page text
        total_jobs_match = re.search(r'(\d+)\s*total\s*jobs', resp.text, re.IGNORECASE)
        if total_jobs_match:
            total_jobs = int(total_jobs_match.group(1))
            estimated_pages = min((total_jobs // 15) + 1, max_pages)
        else:
            estimated_pages = max_pages

        # Fetch additional pages (levels.fyi uses offset-based pagination)
        for page in range(2, estimated_pages + 1):
            try:
                offset = (page - 1) * 15
                page_url = f"{base_url}?offset={offset}"
                resp = requests.get(page_url, headers=HEADERS, timeout=15)

                if resp.status_code != 200:
                    break

                page_jobs = _parse_levelsfyi_page(resp.text)
                new_count = 0
                for job in page_jobs:
                    title_key = (job['title'], job.get('location', ''))
                    if title_key not in seen_titles:
                        seen_titles.add(title_key)
                        all_jobs.append(job)
                        new_count += 1

                print(f"    Page {page}: {new_count} new jobs")

                # Stop if we got no new jobs (reached end or duplicate page)
                if new_count == 0:
                    break

            except requests.RequestException:
                break

        print(f"  ✓ Total: {len(all_jobs)} unique jobs from levels.fyi")
        return all_jobs

    except requests.RequestException as e:
        print(f"  ✗ Failed to fetch from levels.fyi: {e}")
        return []


def _parse_levelsfyi_page(html: str) -> list[dict]:
    """
    Parse job listings from a levels.fyi page.
    Levels.fyi embeds job data as JSON in script tags (Next.js __NEXT_DATA__).
    """
    jobs = []
    soup = BeautifulSoup(html, 'html.parser')

    # Method 1: Extract from __NEXT_DATA__ script tag (primary method)
    next_data = soup.find('script', id='__NEXT_DATA__')
    if next_data and next_data.string:
        try:
            data = json.loads(next_data.string)
            page_props = data.get('props', {}).get('pageProps', {})

            # Jobs are in initialJobsData.results[0].jobs (company-grouped format)
            jobs_data = page_props.get('initialJobsData', {})
            results = jobs_data.get('results', [])

            # Levels.fyi groups jobs by company - extract jobs from each company
            for company_entry in results:
                company_jobs = company_entry.get('jobs', [])

                for job_entry in company_jobs:
                    title = job_entry.get('title', '')
                    if not title:
                        continue

                    # Locations is an array
                    locations = job_entry.get('locations', [])
                    if locations and isinstance(locations, list):
                        location = locations[0] if isinstance(locations[0], str) else locations[0].get('name', 'Not specified')
                    else:
                        location = job_entry.get('location', 'Not specified')

                    # Try to get department/team
                    department = job_entry.get('team', '') or job_entry.get('department', '')
                    if not department:
                        department = _infer_department(title)

                    jobs.append({
                        'title': title,
                        'location': location,
                        'department': department
                    })

            if jobs:
                return jobs

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            print(f"    Warning: Failed to parse __NEXT_DATA__: {e}")

    # Method 2: Try finding JSON in any script tag
    for script in soup.find_all('script'):
        if script.string and '"results"' in script.string and '"title"' in script.string:
            try:
                # Find JSON object with results
                text = script.string
                start = text.find('{')
                if start >= 0:
                    # Try to parse the whole thing as JSON
                    data = json.loads(text[start:])

                    # Navigate to results
                    results = None
                    if 'results' in data:
                        results = data['results']
                    elif 'props' in data:
                        results = data.get('props', {}).get('pageProps', {}).get('initialJobsData', {}).get('results', [])

                    if results:
                        for job_entry in results:
                            title = job_entry.get('title', '')
                            if not title:
                                continue

                            location = job_entry.get('location', 'Not specified')
                            if isinstance(location, dict):
                                location = location.get('name', 'Not specified')

                            department = _infer_department(title)

                            jobs.append({
                                'title': title,
                                'location': location,
                                'department': department
                            })

                        if jobs:
                            return jobs
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

    # Method 3: Fallback to link parsing (for older versions or changes)
    for link in soup.find_all('a', href=True):
        href = link.get('href', '')
        if '/jobs/' in href and href.count('/') >= 2 and 'company' not in href:
            if any(skip in href for skip in ['/jobs/search', '/jobs/remote', '/jobs/new']):
                continue

            title = link.get_text(strip=True)
            if not title or len(title) < 5:
                continue
            if title.lower() in ['view job', 'apply', 'see all', 'more']:
                continue

            department = _infer_department(title)
            jobs.append({
                'title': title,
                'location': 'Not specified',
                'department': department
            })

    # Deduplicate
    seen = set()
    unique_jobs = []
    for job in jobs:
        key = job['title']
        if key not in seen:
            seen.add(key)
            unique_jobs.append(job)

    return unique_jobs


def _infer_department(title: str) -> str:
    """Infer department from job title keywords."""
    title_lower = title.lower()

    dept_keywords = {
        'Engineering': ['engineer', 'developer', 'sre', 'devops', 'architect', 'tech lead'],
        'Product': ['product manager', 'product owner', 'product analyst'],
        'Design': ['designer', 'ux', 'ui', 'creative'],
        'Data': ['data scientist', 'data engineer', 'data analyst', 'ml', 'machine learning', 'ai'],
        'Sales': ['sales', 'account executive', 'business development', 'bdr', 'sdr'],
        'Marketing': ['marketing', 'growth', 'content', 'brand', 'communications'],
        'Customer Success': ['customer success', 'customer experience', 'support'],
        'Finance': ['finance', 'accounting', 'fp&a', 'controller'],
        'HR': ['recruiter', 'talent', 'people', 'hr', 'human resources'],
        'Legal': ['legal', 'counsel', 'compliance', 'attorney'],
        'Operations': ['operations', 'program manager', 'project manager'],
    }

    for dept, keywords in dept_keywords.items():
        if any(kw in title_lower for kw in keywords):
            return dept

    return 'General'


def fetch_jobs_from_linkedin(company_name: str, max_results: int = 200) -> list[dict]:
    """
    Fetch job listings from LinkedIn's guest API.

    Note: This uses LinkedIn's undocumented guest API which may be rate-limited
    or change without notice. Use as a supplementary source, not primary.

    Args:
        company_name: Company name to search for
        max_results: Maximum jobs to fetch (default 200)

    Returns:
        List of job dicts with title, department, location
    """
    print(f"  Fetching jobs from LinkedIn for '{company_name}' (max: {max_results})...")

    all_jobs = []
    seen_titles = set()

    try:
        # Step 1: Get company ID from typeahead API
        typeahead_url = "https://www.linkedin.com/jobs-guest/api/typeaheadHits"
        resp = requests.get(
            typeahead_url,
            params={
                'typeaheadType': 'COMPANY',
                'query': company_name
            },
            headers={
                **HEADERS,
                'Accept': 'application/json',
            },
            timeout=15
        )

        if resp.status_code != 200:
            print(f"  ✗ LinkedIn typeahead API returned {resp.status_code}")
            return []

        # Parse company ID from response
        company_id = None
        try:
            data = resp.json()
            if isinstance(data, list) and len(data) > 0:
                # Find best match
                for hit in data:
                    hit_name = hit.get('displayName', '').lower()
                    if company_name.lower() in hit_name or hit_name in company_name.lower():
                        company_id = hit.get('id')
                        break
                # Fall back to first result if no exact match
                if not company_id and data:
                    company_id = data[0].get('id')
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        if not company_id:
            print(f"  ✗ Could not find LinkedIn company ID for '{company_name}'")
            return []

        print(f"    Found LinkedIn company ID: {company_id}")

        # Step 2: Fetch jobs with pagination
        search_url = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"

        for start in range(0, max_results, 25):
            try:
                resp = requests.get(
                    search_url,
                    params={
                        'f_C': company_id,
                        'start': start,
                        'count': 25
                    },
                    headers=HEADERS,
                    timeout=15
                )

                if resp.status_code != 200:
                    break

                # LinkedIn returns HTML, not JSON - parse it
                soup = BeautifulSoup(resp.text, 'html.parser')

                # Try multiple selectors - LinkedIn changes their HTML frequently
                job_cards = soup.find_all('div', class_='base-card')

                if not job_cards:
                    job_cards = soup.find_all('div', class_=re.compile(r'base-search-card|job-search-card', re.I))

                if not job_cards:
                    job_cards = soup.find_all('li', class_=re.compile(r'jobs-search|result-card', re.I))

                if not job_cards:
                    # Try finding any div/li that contains job posting structure
                    job_cards = soup.find_all(['div', 'li'], attrs={'data-entity-urn': re.compile(r'jobPosting', re.I)})

                if not job_cards:
                    # Last resort - find all links that look like job postings
                    job_links = soup.find_all('a', href=re.compile(r'/jobs/view/\d+'))
                    job_cards = [link.find_parent(['div', 'li']) for link in job_links if link.find_parent(['div', 'li'])]
                    job_cards = [c for c in job_cards if c]  # Remove None values

                if not job_cards:
                    print(f"    Page {start // 25 + 1}: No job cards found, stopping")
                    break

                new_count = 0
                for card in job_cards:
                    # Extract title - try multiple patterns
                    title_elem = card.find(['h3', 'h4', 'a'], class_=re.compile(r'title|name|base-search-card__title', re.I))
                    if not title_elem:
                        title_elem = card.find('a', href=re.compile(r'/jobs/view/'))
                    if not title_elem:
                        title_elem = card.find(['h3', 'h4'])

                    if not title_elem:
                        continue

                    title = title_elem.get_text(strip=True)
                    if not title or title in seen_titles:
                        continue

                    # Skip navigation/filter text
                    if len(title) < 5 or title.lower() in ['apply', 'save', 'share', 'view']:
                        continue

                    # Extract location
                    location_elem = card.find(class_=re.compile(r'location|job-search-card__location', re.I))
                    if not location_elem:
                        location_elem = card.find('span', class_=re.compile(r'bullet', re.I))
                    location = location_elem.get_text(strip=True) if location_elem else 'Not specified'

                    # Infer department from title
                    department = _infer_department(title)

                    seen_titles.add(title)
                    all_jobs.append({
                        'title': title,
                        'department': department,
                        'location': location
                    })
                    new_count += 1

                print(f"    Page {start // 25 + 1}: {new_count} jobs (total cards: {len(job_cards)})")

                if len(job_cards) < 10:  # Lower threshold since we might miss some
                    break

            except requests.RequestException:
                break

        print(f"  ✓ LinkedIn returned {len(all_jobs)} jobs")
        return all_jobs

    except requests.RequestException as e:
        print(f"  ✗ LinkedIn request failed: {e}")
        return []


def fetch_jobs_direct_careers(careers_url: str, company_name: str) -> list[dict]:
    """
    Fetch jobs from a direct careers page using AI extraction.
    This is a fallback for companies with custom career pages.

    Note: This may not capture all jobs if the page is heavily JS-rendered.
    """
    if not HAS_GEMINI:
        print("  ✗ Gemini not available for AI extraction")
        return []

    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        print("  ✗ GEMINI_API_KEY not set")
        return []

    print(f"  Attempting AI extraction from {careers_url}...")

    try:
        resp = requests.get(careers_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"  ✗ Careers page returned {resp.status_code}")
            return []

        # Clean up HTML
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
            tag.decompose()

        text_content = soup.get_text(separator='\n', strip=True)[:15000]  # Limit text

        # Use Gemini to extract job listings
        client = genai.Client(api_key=api_key)
        model_id = os.environ.get('GEMINI_MODEL', 'gemini-1.5-flash')

        prompt = f"""Extract job listings from this careers page content for {company_name}.
Return a JSON array of job objects with these fields:
- title: job title (required)
- department: team/department if mentioned
- location: work location if mentioned

Only include actual job postings, not category headers or navigation.
If no clear job listings are found, return an empty array [].

Page content:
{text_content}"""

        config = types.GenerateContentConfig(
            response_mime_type="application/json"
        )

        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=config
        )

        result = json.loads(response.text.strip())
        if isinstance(result, list):
            jobs = []
            for item in result:
                if isinstance(item, dict) and item.get('title'):
                    jobs.append({
                        'title': item.get('title', ''),
                        'department': item.get('department', 'General'),
                        'location': item.get('location', 'Not specified')
                    })
            print(f"  ✓ AI extracted {len(jobs)} jobs")
            return jobs

    except Exception as e:
        print(f"  ✗ AI extraction failed: {e}")

    return []


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
