#!/usr/bin/env python3
"""
Background Probe - Company Background Intelligence Gatherer

Gathers fundamental information about competitors from multiple sources:
- Wikipedia: History, founding, key people, funding
- LinkedIn: Employee count, industry, description
- Company About Page: Mission, values, story
- Crunchbase: Funding rounds, investors, valuation
- News: Recent press and announcements
"""

import os
import re
import json
import warnings
import requests
from urllib.parse import quote
from datetime import datetime
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Suppress XML parsing warning when lxml isn't available
warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)

# Optional: Gemini for AI-powered extraction
try:
    from google import genai
    from google.genai import types
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
}


def _is_exact_word_match(company_name: str, title: str) -> bool:
    """
    Check if company_name appears as a whole word in title (not as substring).
    e.g., "Linear" should match "Linear (software)" but NOT "Non-linear editing"
    """
    import re
    # Create pattern that matches the company name as a whole word
    pattern = r'\b' + re.escape(company_name) + r'\b'
    return bool(re.search(pattern, title, re.IGNORECASE))


# Known company Wikipedia page titles for ambiguous names
# Use None to explicitly indicate no Wikipedia page exists (skip searching)
WIKIPEDIA_HINTS = {
    'linear': None,  # Linear App doesn't have a Wikipedia page yet
    'notion': 'Notion (productivity software)',
    'figma': 'Figma',
    'asana': 'Asana (software)',
    'monday': 'Monday.com',
    'monday.com': 'Monday.com',
    'clickup': 'ClickUp',
    'jira': 'Jira (software)',
    'trello': 'Trello',
    'gitlab': 'GitLab',
    'github': 'GitHub',
    'githubprojects': 'GitHub',  # GitHub Projects is a feature of GitHub
    'shortcut': None,  # Shortcut (formerly Clubhouse) doesn't have a Wikipedia page
    'height': None,  # Height App doesn't have a Wikipedia page
    'atlassian': 'Atlassian',
    'confluence': 'Confluence (software)',
    'basecamp': 'Basecamp (software)',
    'airtable': 'Airtable',
    'smartsheet': 'Smartsheet',
    'wrike': 'Wrike',
    'teamwork': 'Teamwork (software)',
}


def fetch_wikipedia_data(company_name: str) -> dict | None:
    """
    Fetch company information from Wikipedia using their API.

    Returns:
        Dict with summary, infobox data (founded, founders, hq, employees, etc.)
    """
    print(f"  Fetching Wikipedia data for '{company_name}'...")

    # Wikipedia API endpoint for searching and getting page content
    search_url = "https://en.wikipedia.org/w/api.php"

    try:
        page_title = None

        # Step 0: Check if we have a known hint for this company
        hint_key = company_name.lower().replace(' ', '')
        if hint_key in WIKIPEDIA_HINTS:
            hint_title = WIKIPEDIA_HINTS[hint_key]

            # None means we know there's no Wikipedia page - skip searching
            if hint_title is None:
                print(f"    No Wikipedia page exists for '{company_name}'")
                return None

            # Try the known page title directly
            check_params = {
                'action': 'query',
                'titles': hint_title,
                'format': 'json'
            }
            resp = requests.get(search_url, params=check_params, headers=HEADERS, timeout=15)
            check_data = resp.json()
            pages = check_data.get('query', {}).get('pages', {})
            # Check if page exists (no -1 pageid)
            if pages and '-1' not in pages:
                page_title = hint_title
                print(f"    Using known page: {page_title}")

        # Step 1: Search for the company page - try multiple search strategies
        if not page_title:
            search_queries = [
                f'"{company_name}" software company',
                f'"{company_name}" (company)',
                f'"{company_name}" (software)',
                f'{company_name} company',
            ]

            for query in search_queries:
                search_params = {
                    'action': 'query',
                    'list': 'search',
                    'srsearch': query,
                    'format': 'json',
                    'srlimit': 5
                }
                resp = requests.get(search_url, params=search_params, headers=HEADERS, timeout=15)
                search_results = resp.json()

                search_hits = search_results.get('query', {}).get('search', [])

                # Look for a page that's clearly about a company
                for hit in search_hits:
                    title = hit.get('title', '')
                    snippet = hit.get('snippet', '').lower()
                    title_lower = title.lower()

                    # CRITICAL: Check for exact word match, not substring
                    if not _is_exact_word_match(company_name, title):
                        continue

                    # Check if it's a company page
                    is_company_page = (
                        'company' in title_lower or 'software' in title_lower or
                        'software' in snippet or 'founded' in snippet or
                        'startup' in snippet or 'inc' in snippet or
                        'corporation' in snippet or 'saas' in snippet
                    )

                    # Or exact match with (company) or (software)
                    if f"{company_name.lower()} (company)" in title_lower:
                        page_title = title
                        break
                    if f"{company_name.lower()} (software)" in title_lower:
                        page_title = title
                        break
                    if is_company_page:
                        page_title = title
                        break

                if page_title:
                    break

        if not page_title:
            # Last resort: just use company name if page exists with exact match
            search_params = {
                'action': 'query',
                'list': 'search',
                'srsearch': company_name,
                'format': 'json',
                'srlimit': 5
            }
            resp = requests.get(search_url, params=search_params, headers=HEADERS, timeout=15)
            search_results = resp.json()
            search_hits = search_results.get('query', {}).get('search', [])

            if search_hits:
                # Only use if it's an exact word match (not substring)
                for hit in search_hits:
                    if _is_exact_word_match(company_name, hit.get('title', '')):
                        page_title = hit.get('title')
                        break

        if not page_title:
            print(f"    No relevant Wikipedia page found for '{company_name}'")
            return None

        print(f"    Found Wikipedia page: {page_title}")

        # Step 2: Get page content (extract + HTML for infobox)
        content_params = {
            'action': 'query',
            'titles': page_title,
            'prop': 'extracts|revisions',
            'exintro': True,
            'explaintext': True,
            'rvprop': 'content',
            'rvslots': 'main',
            'format': 'json'
        }
        resp = requests.get(search_url, params=content_params, headers=HEADERS, timeout=15)
        content_data = resp.json()

        pages = content_data.get('query', {}).get('pages', {})
        page = list(pages.values())[0] if pages else {}

        # Get summary extract
        summary = page.get('extract', '')[:1000]  # First 1000 chars

        # Get full wikitext to parse infobox
        revisions = page.get('revisions', [])
        wikitext = ''
        if revisions:
            wikitext = revisions[0].get('slots', {}).get('main', {}).get('*', '')

        # Parse infobox from wikitext
        infobox = _parse_wikipedia_infobox(wikitext)

        result = {
            'source': 'wikipedia',
            'page_title': page_title,
            'url': f"https://en.wikipedia.org/wiki/{quote(page_title.replace(' ', '_'))}",
            'summary': summary,
            **infobox
        }

        print(f"    ✓ Wikipedia data retrieved")
        return result

    except Exception as e:
        print(f"    ✗ Wikipedia fetch failed: {e}")
        return None


def _parse_wikipedia_infobox(wikitext: str) -> dict:
    """
    Parse company infobox from Wikipedia wikitext.
    Extracts: founded, founders, headquarters, employees, revenue, industry, etc.
    """
    infobox = {}

    if not wikitext:
        return infobox

    # Find infobox section
    infobox_match = re.search(r'\{\{Infobox[^}]*company[^}]*\n(.*?)\n\}\}', wikitext, re.IGNORECASE | re.DOTALL)
    if not infobox_match:
        # Try generic infobox
        infobox_match = re.search(r'\{\{Infobox[^\n]*\n(.*?)\n\}\}', wikitext, re.IGNORECASE | re.DOTALL)

    if not infobox_match:
        return infobox

    infobox_text = infobox_match.group(1)

    # Fields to extract
    fields = {
        'founded': ['founded', 'foundation', 'established'],
        'founders': ['founder', 'founders'],
        'headquarters': ['headquarters', 'hq_location', 'location_city', 'location'],
        'employees': ['num_employees', 'employees', 'staff'],
        'revenue': ['revenue'],
        'industry': ['industry', 'industries'],
        'products': ['products', 'services'],
        'website': ['website', 'url', 'homepage'],
        'type': ['type'],  # Public/Private
        'key_people': ['key_people', 'leadership'],
    }

    for field_name, patterns in fields.items():
        for pattern in patterns:
            # Match | field_name = value (capture until next | field or end)
            # Use a more permissive regex that captures template content
            match = re.search(rf'\|\s*{pattern}\s*=\s*(.+?)(?=\n\s*\||\n\}}|$)', infobox_text, re.IGNORECASE | re.DOTALL)
            if match:
                value = match.group(1).strip()

                # First, try to find a year in {{Start date...}} or {{founded...}} templates
                date_match = re.search(r'\{\{[^}]*(?:date|founded)[^}]*\|(\d{4})', value, re.IGNORECASE)
                if date_match:
                    value = date_match.group(1)
                else:
                    # Try to extract just a year if present
                    year_match = re.search(r'\b(19\d{2}|20\d{2})\b', value)
                    if year_match and field_name in ['founded']:
                        value = year_match.group(1)
                    else:
                        # Handle {{ubl|Name1|Name2}} or {{Plainlist|...}} -> Name1, Name2
                        list_match = re.search(r'\{\{(?:ubl|Plainlist|hlist|unbulleted list)[^|]*\|(.+?)\}\}', value, re.IGNORECASE | re.DOTALL)
                        if list_match:
                            items = list_match.group(1).split('|')
                            cleaned_items = []
                            for item in items:
                                item = re.sub(r'\[\[([^|\]]+\|)?([^\]]+)\]\]', r'\2', item)
                                item = item.strip()
                                if item and not item.startswith('*') and len(item) > 1:
                                    cleaned_items.append(item)
                            value = ', '.join(cleaned_items[:5])  # Limit to 5 items
                        else:
                            # Remove all templates
                            value = re.sub(r'\{\{[^}]*\}\}', '', value)

                # Clean wiki markup
                value = re.sub(r'\[\[([^|\]]+\|)?([^\]]+)\]\]', r'\2', value)  # [[link|text]] -> text
                value = re.sub(r"'''?", '', value)  # Remove bold/italic
                value = re.sub(r'<[^>]+>', '', value)  # Remove HTML tags
                value = re.sub(r'&nbsp;', ' ', value)  # Replace nbsp
                value = re.sub(r'\s+', ' ', value).strip()

                # Remove trailing/leading punctuation
                value = value.strip('.,;: ')

                # Filter out Wikipedia infobox parsing artifacts
                # (junk like "| homepage = |" or empty template markers)
                if '|' in value or '=' in value or value.startswith('{') or value.startswith('['):
                    continue  # Skip this pattern, try next one

                if value and value.lower() not in ['n/a', 'unknown', ''] and len(value) > 1:
                    infobox[field_name] = value
                break

    return infobox


def fetch_linkedin_company_data(company_name: str) -> dict | None:
    """
    Fetch company information from LinkedIn's guest API.

    Note: LinkedIn actively blocks scraping. This uses public endpoints
    but may be unreliable. Use as supplementary source.

    Returns:
        Dict with employee count, industry, description, follower count
    """
    print(f"  Fetching LinkedIn data for '{company_name}'...")

    try:
        # Step 1: Get company ID from typeahead
        typeahead_url = "https://www.linkedin.com/jobs-guest/api/typeaheadHits"
        resp = requests.get(
            typeahead_url,
            params={'typeaheadType': 'COMPANY', 'query': company_name},
            headers=HEADERS,
            timeout=15
        )

        if resp.status_code != 200:
            print(f"    ✗ LinkedIn typeahead returned {resp.status_code}")
            return None

        data = resp.json()
        if not isinstance(data, list) or not data:
            print(f"    ✗ No LinkedIn company found")
            return None

        # Find best match
        company_data = None
        for hit in data:
            hit_name = hit.get('displayName', '').lower()
            if company_name.lower() in hit_name or hit_name in company_name.lower():
                company_data = hit
                break

        if not company_data:
            company_data = data[0]

        company_id = company_data.get('id')
        display_name = company_data.get('displayName', company_name)

        print(f"    Found LinkedIn company: {display_name} (ID: {company_id})")

        # Step 2: Try to get more company details from public page
        # LinkedIn's public company page often has some data
        company_url = f"https://www.linkedin.com/company/{company_id}"

        result = {
            'source': 'linkedin',
            'company_id': company_id,
            'name': display_name,
            'url': company_url,
        }

        # Try to scrape public company page for more details
        try:
            page_resp = requests.get(company_url, headers=HEADERS, timeout=15)
            if page_resp.status_code == 200:
                soup = BeautifulSoup(page_resp.text, 'html.parser')

                # Look for employee count in page
                employee_match = re.search(r'(\d[\d,]+)\s*(?:employees|staff|people)', page_resp.text, re.IGNORECASE)
                if employee_match:
                    result['employees'] = employee_match.group(1).replace(',', '')

                # Look for follower count
                follower_match = re.search(r'(\d[\d,]+)\s*followers', page_resp.text, re.IGNORECASE)
                if follower_match:
                    result['followers'] = follower_match.group(1).replace(',', '')

                # Try to get description from meta tags
                meta_desc = soup.find('meta', {'name': 'description'})
                if meta_desc:
                    result['description'] = meta_desc.get('content', '')[:500]
        except Exception:
            pass

        print(f"    ✓ LinkedIn data retrieved")
        return result

    except Exception as e:
        print(f"    ✗ LinkedIn fetch failed: {e}")
        return None


def fetch_company_about_page(domain: str, company_name: str) -> dict | None:
    """
    Fetch and extract information from a company's About page.
    Uses AI to extract key information if available.

    Returns:
        Dict with mission, description, team info, story
    """
    print(f"  Fetching About page for {domain}...")

    # Common about page paths
    about_paths = [
        '/about',
        '/about-us',
        '/company',
        '/about/company',
        '/company/about',
        '/who-we-are',
    ]

    if not domain.startswith('http'):
        domain = f'https://{domain}'

    about_content = None
    about_url = None

    for path in about_paths:
        try:
            url = f"{domain.rstrip('/')}{path}"
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code == 200 and len(resp.text) > 1000:
                about_content = resp.text
                about_url = url
                print(f"    Found About page: {url}")
                break
        except Exception:
            continue

    if not about_content:
        print(f"    ✗ No About page found")
        return None

    # Clean HTML and extract text
    soup = BeautifulSoup(about_content, 'html.parser')

    # Remove scripts, styles, nav, footer
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()

    text_content = soup.get_text(separator='\n', strip=True)[:10000]

    # Try AI extraction if available
    if HAS_GEMINI:
        result = _extract_about_with_ai(text_content, company_name, about_url)
        if result:
            return result

    # Fallback: Basic extraction
    result = {
        'source': 'about_page',
        'url': about_url,
        'raw_text': text_content[:2000],
    }

    # Try to find mission statement
    mission_match = re.search(r'(?:mission|our mission)[:\s]+([^.]+\.)', text_content, re.IGNORECASE)
    if mission_match:
        result['mission'] = mission_match.group(1).strip()

    print(f"    ✓ About page data retrieved")
    return result


def _extract_about_with_ai(text_content: str, company_name: str, url: str) -> dict | None:
    """Use Gemini to extract structured info from About page."""
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        return None

    try:
        client = genai.Client(api_key=api_key)
        model_id = os.environ.get('GEMINI_MODEL', 'gemini-2.0-flash')

        prompt = f"""Extract key company information from this About page text for {company_name}.
Return a JSON object with these fields (use null if not found):
- mission: Company mission statement (1-2 sentences)
- description: What the company does (2-3 sentences)
- founded: Year founded
- founders: Founder names (comma-separated)
- headquarters: HQ location
- values: Core company values (comma-separated list)
- story: Brief company origin story (2-3 sentences)

About page text:
{text_content[:8000]}"""

        config = types.GenerateContentConfig(
            response_mime_type="application/json"
        )

        response = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=config
        )

        result = json.loads(response.text.strip())
        result['source'] = 'about_page'
        result['url'] = url
        return result

    except Exception as e:
        print(f"    AI extraction failed: {e}")
        return None


def fetch_crunchbase_data(company_name: str) -> dict | None:
    """
    Fetch company funding data from Crunchbase.

    Note: Crunchbase has rate limits and may require API key for full access.
    This uses their public search/autocomplete which has limited data.

    Returns:
        Dict with funding info, investors, valuation
    """
    print(f"  Fetching Crunchbase data for '{company_name}'...")

    try:
        # Crunchbase autocomplete API (public, limited)
        search_url = "https://www.crunchbase.com/v4/data/autocompletes"
        params = {
            'query': company_name,
            'collection_ids': 'organizations',
            'limit': 5
        }

        resp = requests.get(search_url, params=params, headers={
            **HEADERS,
            'Accept': 'application/json',
        }, timeout=15)

        if resp.status_code != 200:
            # Try alternative: scrape search results page
            return _scrape_crunchbase_search(company_name)

        data = resp.json()
        entities = data.get('entities', [])

        if not entities:
            return _scrape_crunchbase_search(company_name)

        # Find best match
        org = None
        for entity in entities:
            props = entity.get('identifier', {})
            name = props.get('value', '').lower()
            if company_name.lower() in name or name in company_name.lower():
                org = entity
                break

        if not org:
            org = entities[0]

        props = org.get('identifier', {})
        permalink = props.get('permalink', '')

        result = {
            'source': 'crunchbase',
            'name': props.get('value'),
            'url': f"https://www.crunchbase.com/organization/{permalink}" if permalink else None,
            'permalink': permalink,
        }

        # Try to get more details from the organization page
        if permalink:
            more_data = _scrape_crunchbase_org(permalink)
            if more_data:
                result.update(more_data)

        print(f"    ✓ Crunchbase data retrieved")
        return result

    except Exception as e:
        print(f"    ✗ Crunchbase fetch failed: {e}")
        return None


def _scrape_crunchbase_search(company_name: str) -> dict | None:
    """Fallback: Scrape Crunchbase search results."""
    try:
        search_url = f"https://www.crunchbase.com/textsearch?q={quote(company_name)}"
        resp = requests.get(search_url, headers=HEADERS, timeout=15)

        if resp.status_code != 200:
            return None

        # Crunchbase uses client-side rendering, so limited data available
        # without JavaScript execution. Return None for now.
        return None

    except Exception:
        return None


def _scrape_crunchbase_org(permalink: str) -> dict | None:
    """Try to scrape additional data from Crunchbase org page."""
    try:
        url = f"https://www.crunchbase.com/organization/{permalink}"
        resp = requests.get(url, headers=HEADERS, timeout=15)

        if resp.status_code != 200:
            return None

        result = {}
        text = resp.text

        # Try to extract funding from page text
        funding_match = re.search(r'Total Funding[:\s]*\$?([\d.]+[BMK]?)', text, re.IGNORECASE)
        if funding_match:
            result['total_funding'] = funding_match.group(1)

        # Last funding round
        round_match = re.search(r'(Series [A-Z]|Seed|IPO)[^$]*\$?([\d.]+[BMK]?)', text, re.IGNORECASE)
        if round_match:
            result['last_round'] = f"{round_match.group(1)}: ${round_match.group(2)}"

        # Employee count
        emp_match = re.search(r'(\d[\d,]*)\s*(?:employees|staff)', text, re.IGNORECASE)
        if emp_match:
            result['employees'] = emp_match.group(1).replace(',', '')

        return result if result else None

    except Exception:
        return None


def fetch_recent_news(company_name: str, max_results: int = 5) -> list[dict]:
    """
    Fetch recent news articles about the company.
    Uses Google News RSS feed.

    Returns:
        List of news items with title, url, date
    """
    print(f"  Fetching recent news for '{company_name}'...")

    try:
        # Google News RSS feed
        news_url = f"https://news.google.com/rss/search?q={quote(company_name)}+company&hl=en-US&gl=US&ceid=US:en"

        resp = requests.get(news_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            print(f"    ✗ News fetch failed: {resp.status_code}")
            return []

        # Try xml parser first, fall back to html.parser
        try:
            soup = BeautifulSoup(resp.text, 'xml')
        except Exception:
            soup = BeautifulSoup(resp.text, 'html.parser')

        items = soup.find_all('item')[:max_results]

        news = []
        for item in items:
            title = item.find('title')
            link = item.find('link')
            pub_date = item.find('pubDate')

            if title and link:
                news.append({
                    'title': title.get_text(strip=True),
                    'url': link.get_text(strip=True),
                    'date': pub_date.get_text(strip=True) if pub_date else None
                })

        print(f"    ✓ Found {len(news)} recent news items")
        return news

    except Exception as e:
        print(f"    ✗ News fetch failed: {e}")
        return []


def fetch_github_data(company_name: str) -> dict | None:
    """
    Fetch company's GitHub organization data.
    Shows open source activity and developer engagement.

    Returns:
        Dict with repos, stars, contributors, languages
    """
    print(f"  Fetching GitHub data for '{company_name}'...")

    # Common GitHub org name patterns
    org_names = [
        company_name.lower().replace(' ', ''),
        company_name.lower().replace(' ', '-'),
        company_name.lower(),
    ]

    for org_name in org_names:
        try:
            # GitHub API (unauthenticated - 60 requests/hour limit)
            api_url = f"https://api.github.com/orgs/{org_name}"
            resp = requests.get(api_url, headers={
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': 'background-probe/1.0'
            }, timeout=15)

            if resp.status_code == 200:
                data = resp.json()

                result = {
                    'source': 'github',
                    'org_name': data.get('login'),
                    'url': data.get('html_url'),
                    'description': data.get('description'),
                    'public_repos': data.get('public_repos'),
                    'followers': data.get('followers'),
                    'created_at': data.get('created_at'),
                    'blog': data.get('blog'),
                }

                # Get top repos
                repos_url = f"https://api.github.com/orgs/{org_name}/repos?sort=stars&per_page=5"
                repos_resp = requests.get(repos_url, headers={
                    'Accept': 'application/vnd.github.v3+json',
                    'User-Agent': 'background-probe/1.0'
                }, timeout=15)

                if repos_resp.status_code == 200:
                    repos = repos_resp.json()
                    result['top_repos'] = [
                        {
                            'name': r.get('name'),
                            'stars': r.get('stargazers_count'),
                            'language': r.get('language'),
                            'description': r.get('description', '')[:100]
                        }
                        for r in repos[:5]
                    ]

                    # Calculate total stars
                    total_stars = sum(r.get('stargazers_count', 0) for r in repos)
                    result['total_stars'] = total_stars

                print(f"    ✓ GitHub data retrieved ({data.get('public_repos')} repos)")
                return result

        except Exception:
            continue

    print(f"    ✗ No GitHub organization found")
    return None


def gather_company_background(
    company_name: str,
    domain: str = None,
    include_news: bool = True,
    include_github: bool = True
) -> dict:
    """
    Main function to gather comprehensive background information about a company.

    Args:
        company_name: Company name to research
        domain: Company website domain (optional, for About page)
        include_news: Whether to fetch recent news
        include_github: Whether to fetch GitHub data

    Returns:
        Dict with all gathered background information
    """
    print(f"\n{'='*60}")
    print(f"  BACKGROUND PROBE: {company_name}")
    print(f"{'='*60}")

    result = {
        'company_name': company_name,
        'domain': domain,
        'gathered_at': datetime.now().isoformat(),
        'sources': {}
    }

    # 1. Wikipedia (most reliable for established companies)
    wiki_data = fetch_wikipedia_data(company_name)
    if wiki_data:
        result['sources']['wikipedia'] = wiki_data

    # 2. LinkedIn (employee count, growth)
    linkedin_data = fetch_linkedin_company_data(company_name)
    if linkedin_data:
        result['sources']['linkedin'] = linkedin_data

    # 3. Company About Page
    if domain:
        about_data = fetch_company_about_page(domain, company_name)
        if about_data:
            result['sources']['about_page'] = about_data

    # 4. Crunchbase (funding data)
    crunchbase_data = fetch_crunchbase_data(company_name)
    if crunchbase_data:
        result['sources']['crunchbase'] = crunchbase_data

    # 5. Recent News
    if include_news:
        news = fetch_recent_news(company_name)
        if news:
            result['sources']['news'] = news

    # 6. GitHub (open source activity)
    if include_github:
        github_data = fetch_github_data(company_name)
        if github_data:
            result['sources']['github'] = github_data

    # Synthesize key facts from all sources
    result['summary'] = _synthesize_background(result)

    return result


def _synthesize_background(data: dict) -> dict:
    """
    Synthesize key facts from all gathered sources into a unified summary.
    Prioritizes most reliable sources for each data point.
    """
    summary = {
        'name': data.get('company_name'),
        'description': None,
        'founded': None,
        'founders': None,
        'headquarters': None,
        'employees': None,
        'funding': None,
        'industry': None,
        'website': data.get('domain'),
    }

    sources = data.get('sources', {})

    # Wikipedia is most reliable for historical facts
    wiki = sources.get('wikipedia', {})
    if wiki:
        summary['founded'] = summary['founded'] or wiki.get('founded')
        summary['founders'] = summary['founders'] or wiki.get('founders')
        summary['headquarters'] = summary['headquarters'] or wiki.get('headquarters')
        summary['employees'] = summary['employees'] or wiki.get('employees')
        summary['industry'] = summary['industry'] or wiki.get('industry')
        summary['description'] = summary['description'] or wiki.get('summary')

    # LinkedIn for current employee count
    linkedin = sources.get('linkedin', {})
    if linkedin:
        summary['employees'] = linkedin.get('employees') or summary['employees']
        summary['description'] = summary['description'] or linkedin.get('description')

    # About page for mission/description
    about = sources.get('about_page', {})
    if about:
        summary['mission'] = about.get('mission')
        summary['description'] = summary['description'] or about.get('description')
        summary['founded'] = summary['founded'] or about.get('founded')
        summary['founders'] = summary['founders'] or about.get('founders')

    # Crunchbase for funding
    crunchbase = sources.get('crunchbase', {})
    if crunchbase:
        summary['funding'] = crunchbase.get('total_funding')
        summary['last_funding_round'] = crunchbase.get('last_round')

    # GitHub stats
    github = sources.get('github', {})
    if github:
        summary['github_repos'] = github.get('public_repos')
        summary['github_stars'] = github.get('total_stars')

    # Clean up None values
    summary = {k: v for k, v in summary.items() if v is not None}

    return summary


# --- CLI Interface ---
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Background Probe - Company Intelligence")
    parser.add_argument("company", help="Company name to research")
    parser.add_argument("--domain", "-d", help="Company website domain")
    parser.add_argument("--no-news", action="store_true", help="Skip news fetching")
    parser.add_argument("--no-github", action="store_true", help="Skip GitHub fetching")
    parser.add_argument("--output", "-o", help="Output JSON file")

    args = parser.parse_args()

    result = gather_company_background(
        company_name=args.company,
        domain=args.domain,
        include_news=not args.no_news,
        include_github=not args.no_github
    )

    # Print summary
    print(f"\n{'='*60}")
    print("  BACKGROUND SUMMARY")
    print(f"{'='*60}")

    summary = result.get('summary', {})
    for key, value in summary.items():
        if value:
            print(f"  {key}: {value}")

    # Save to file if requested
    if args.output:
        with open(args.output, 'w') as f:
            json.dump(result, f, indent=2)
        print(f"\n✓ Full results saved to {args.output}")

    # Print news if available
    news = result.get('sources', {}).get('news', [])
    if news:
        print(f"\n  Recent News:")
        for item in news[:3]:
            print(f"    - {item['title'][:80]}...")
