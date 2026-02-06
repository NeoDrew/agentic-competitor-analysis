import time
import os
import json
from urllib.parse import urlparse
from google import genai
from google.genai import types
from dotenv import load_dotenv
import requests
from bs4 import BeautifulSoup
import re
from ghost_probe import detect_ats


def _verify_ashby_exists(slug: str) -> bool:
    """Check if an Ashby job board actually exists and has jobs."""
    api_url = 'https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobBoardWithTeams'
    payload = {
        "operationName": "ApiJobBoardWithTeams",
        "variables": {"organizationHostedJobsPageName": slug},
        "query": """query ApiJobBoardWithTeams($organizationHostedJobsPageName: String!) {
            jobBoard: jobBoardWithTeams(organizationHostedJobsPageName: $organizationHostedJobsPageName) {
                jobPostings { id }
            }
        }"""
    }
    try:
        resp = requests.post(api_url, json=payload, timeout=10)
        data = resp.json()
        job_board = data.get('data', {}).get('jobBoard')
        if job_board and job_board.get('jobPostings'):
            return len(job_board['jobPostings']) > 0
    except Exception:
        pass
    return False


def _verify_greenhouse_exists(slug: str) -> bool:
    """Check if a Greenhouse job board actually exists and has real job listings."""
    urls = [
        f"https://job-boards.greenhouse.io/{slug}",
        f"https://boards.greenhouse.io/{slug}"
    ]
    for url in urls:
        try:
            resp = requests.get(url, timeout=10, headers={
                                'User-Agent': 'Sentinel/1.0'}, allow_redirects=True)
            if resp.status_code != 200:
                continue

            # Check if we stayed on greenhouse.io (not redirected to company's own careers page)
            if 'greenhouse.io' not in resp.url:
                continue

            # Look for actual job posting indicators
            # Real greenhouse boards have job cards with specific structure
            text = resp.text.lower()
            has_jobs_link = '/jobs/' in text
            has_opening = 'opening' in text or 'position' in text
            has_apply = 'apply' in text

            # Must have job links AND (opening/position OR apply button)
            if has_jobs_link and (has_opening or has_apply):
                # Additional check: count job links - should be more than just navigation
                job_link_count = text.count('/jobs/')
                if job_link_count >= 3:  # At least 3 job links suggests real listings
                    return True
        except Exception:
            pass
    return False


def _verify_lever_exists(slug: str) -> bool:
    """Check if a Lever job board actually exists."""
    url = f"https://jobs.lever.co/{slug}"
    try:
        resp = requests.get(url, timeout=10, headers={
                            'User-Agent': 'Sentinel/1.0'})
        # Lever returns 404 for invalid boards
        if resp.status_code == 200 and 'posting' in resp.text.lower():
            return True
    except Exception:
        pass
    return False


# Known companies with custom career pages (no standard ATS)
# Maps product names and company names to their job data sources
CUSTOM_CAREERS = {
    # Atlassian products
    'atlassian': {
        'careers_url': 'https://www.atlassian.com/company/careers/all-jobs',
        'levelsfyi_slug': 'atlassian',
        'pricing_url': 'https://www.atlassian.com/software/jira/pricing',
    },
    'jira': {
        'careers_url': 'https://www.atlassian.com/company/careers/all-jobs',
        'levelsfyi_slug': 'atlassian',
        'pricing_url': 'https://www.atlassian.com/software/jira/pricing',
    },
    'confluence': {
        'careers_url': 'https://www.atlassian.com/company/careers/all-jobs',
        'levelsfyi_slug': 'atlassian',
        'pricing_url': 'https://www.atlassian.com/software/confluence/pricing',
    },
    'trello': {
        'careers_url': 'https://www.atlassian.com/company/careers/all-jobs',
        'levelsfyi_slug': 'atlassian',
        'pricing_url': 'https://trello.com/pricing',
    },
    # Microsoft products
    'microsoft': {
        'careers_url': 'https://careers.microsoft.com/',
        'levelsfyi_slug': 'microsoft',
        'pricing_url': None,
    },
    'azure devops': {
        'careers_url': 'https://careers.microsoft.com/',
        'levelsfyi_slug': 'microsoft',
        'pricing_url': 'https://azure.microsoft.com/en-us/pricing/details/devops/azure-devops-services/',
    },
    'azure': {
        'careers_url': 'https://careers.microsoft.com/',
        'levelsfyi_slug': 'microsoft',
    },
    # GitHub (Microsoft subsidiary but separate brand)
    'github': {
        'careers_url': 'https://github.com/about/careers',
        'levelsfyi_slug': 'github',
        'pricing_url': 'https://github.com/pricing',
    },
    'github projects': {
        'careers_url': 'https://github.com/about/careers',
        'levelsfyi_slug': 'github',
        'pricing_url': 'https://github.com/pricing',
    },
    # GitLab
    'gitlab': {
        'careers_url': 'https://about.gitlab.com/jobs/',
        'levelsfyi_slug': 'gitlab',
        'pricing_url': 'https://about.gitlab.com/pricing/',
    },
    # Monday.com
    'monday.com': {
        'careers_url': 'https://monday.com/careers',
        'levelsfyi_slug': 'mondaycom',
        'pricing_url': 'https://monday.com/pricing',
    },
    'monday': {
        'careers_url': 'https://monday.com/careers',
        'levelsfyi_slug': 'mondaycom',
        'pricing_url': 'https://monday.com/pricing',
    },
    # Other major companies
    'notion': {
        'careers_url': 'https://www.notion.so/careers',
        'levelsfyi_slug': 'notion',
        'pricing_url': 'https://www.notion.so/pricing',
    },
    'figma': {
        'careers_url': 'https://www.figma.com/careers/',
        'levelsfyi_slug': 'figma',
        'pricing_url': 'https://www.figma.com/pricing/',
    },
    'stripe': {
        'careers_url': 'https://stripe.com/jobs',
        'levelsfyi_slug': 'stripe',
        'pricing_url': 'https://stripe.com/pricing',
    },
    'asana': {
        'careers_url': 'https://asana.com/jobs',
        'levelsfyi_slug': 'asana',
        'pricing_url': 'https://asana.com/pricing',
    },
    'clickup': {
        'careers_url': 'https://clickup.com/careers',
        'levelsfyi_slug': 'clickup',
        'pricing_url': 'https://clickup.com/pricing',
    },
    'linear': {
        'levelsfyi_slug': 'linear',
        'pricing_url': 'https://linear.app/pricing',
    },
}


def get_custom_careers_info(company_name: str) -> dict | None:
    """Get custom careers info for companies without standard ATS."""
    name_lower = company_name.lower().replace(' ', '').replace('.', '')
    for key, info in CUSTOM_CAREERS.items():
        if key.lower().replace(' ', '').replace('.', '') == name_lower:
            return info
    return None


def try_common_ats_urls(company_name: str) -> dict | None:
    """
    Fallback: directly check if common ATS URLs exist for a company.
    Validates that the ATS actually has job listings (not just 200 status).
    """
    # Normalize company name for URL (lowercase, remove spaces/punctuation)
    slug = company_name.lower().replace(" ", "").replace(".", "").replace("-", "")
    slug_hyphen = company_name.lower().replace(" ", "-").replace(".", "")

    # Try Greenhouse first (most common)
    for s in [slug, slug_hyphen]:
        if _verify_greenhouse_exists(s):
            return {"type": "greenhouse", "url": f"https://job-boards.greenhouse.io/{s}"}

    # Try Lever
    for s in [slug, slug_hyphen]:
        if _verify_lever_exists(s):
            return {"type": "lever", "url": f"https://jobs.lever.co/{s}"}

    # Try Ashby (verify via API)
    for s in [slug, slug_hyphen, company_name]:
        if _verify_ashby_exists(s):
            return {"type": "ashby", "url": f"https://jobs.ashbyhq.com/{s}"}

    return None


load_dotenv()


def suggest_competitors(user_description: str, num_competitors: int = 5, max_retries: int = 5) -> list[dict]:
    """
    Uses Gemini to suggest competitors based on a product/company description.
    Includes exponential backoff retry for transient API errors.

    Args:
        user_description: Description of the product/company to find competitors for
        num_competitors: Number of competitors to return (default 5)
        max_retries: Maximum retry attempts for transient errors

    Returns:
        List of dicts with 'name' and 'domain' keys
    """
    import time

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError(
            "Error: GEMINI_API_KEY not set. Please set it in your environment variables.")

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    system_instruction = """You are a competitive intelligence analyst.
When given a product or company description, identify direct competitors in that market.
Return a JSON array of objects with "name" and "domain" fields.
The domain should be the company's main website (e.g., "asana.com", "linear.app").
Example output: [{"name": "Asana", "domain": "asana.com"}, {"name": "Linear", "domain": "linear.app"}]
"""

    user_prompt = f"""Identify {num_competitors} direct competitors for the following:

{user_description}

Return a JSON array with name and domain for each competitor."""

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        response_mime_type="application/json"
    )

    # Transient error indicators
    retryable_errors = ['429', '503',
                        'RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'overloaded']

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=user_prompt,
                config=config
            )

            # Parse the JSON response
            result_text = response.text.strip()
            # Clean markdown code blocks if present
            if result_text.startswith("```"):
                result_text = result_text.split("\n", 1)[1]
                result_text = result_text.rsplit("```", 1)[0]

            competitors = json.loads(result_text)

            if isinstance(competitors, list):
                return competitors[:num_competitors]
            else:
                print(f"Unexpected response format: {result_text}")
                return []

        except json.JSONDecodeError as e:
            print(f"Failed to parse Gemini response as JSON: {e}")
            print(f"Raw response: {response.text[:500]}")
            return []

        except Exception as e:
            error_str = str(e)
            is_retryable = any(code in error_str for code in retryable_errors)

            if is_retryable and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2  # 2s, 4s, 8s, 16s, 32s
                print(
                    f"⚠️  API overloaded (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"Gemini API error: {e}")
                return []

    print("Max retries exceeded for suggest_competitors")
    return []

# 2. The Hands: Find the URLs


def _find_pricing_link_from_page(base_url: str, headers: dict) -> str | None:
    """
    Scrape a page (usually homepage) to find a pricing link in navigation.
    Returns the full pricing URL if found, None otherwise.
    """
    try:
        resp = requests.get(base_url, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')

        # Look for links containing "pricing", "plans", "price" in href or text
        pricing_keywords = ['pricing', 'plans', 'price', 'packages']

        for link in soup.find_all('a', href=True):
            href = link.get('href', '').lower()
            text = link.get_text(strip=True).lower()

            # Check if link text or href contains pricing keywords
            for keyword in pricing_keywords:
                if keyword in href or keyword in text:
                    full_url = href

                    # Handle relative URLs
                    if href.startswith('/'):
                        parsed = urlparse(base_url)
                        full_url = f"{parsed.scheme}://{parsed.netloc}{href}"
                    elif not href.startswith('http'):
                        full_url = f"{base_url.rstrip('/')}/{href}"

                    # Verify the URL actually works
                    if verify_url(full_url, headers):
                        return full_url

        return None
    except Exception:
        return None


def verify_url(url: str, headers: dict) -> bool:
    """
    Check if a URL is accessible and returns valid content.
    Uses GET with stream=True to avoid downloading full content,
    since many sites block HEAD requests.
    """
    try:
        # Use GET with stream=True - more reliable than HEAD
        # Many sites block HEAD or return incorrect status codes
        resp = requests.get(url, headers=headers, timeout=10,
                            allow_redirects=True, stream=True)
        # Check for success status and that we didn't get redirected to a 404/error page
        if resp.status_code == 200:
            # Quick check that it's actually a pricing/content page, not a redirect to homepage
            content_type = resp.headers.get('content-type', '')
            if 'text/html' in content_type or 'application/json' in content_type:
                return True
        return False
    except requests.RequestException:
        return False


def find_company_links(competitor: dict) -> dict:
    """
    Build company links from competitor data (name + domain from Gemini).

    Args:
        competitor: Dict with 'name' and 'domain' keys

    Returns:
        Dict with domain, pricing_url, careers_url, ats_url
    """
    name = competitor.get("name", "Unknown")
    domain = competitor.get("domain", "")

    print(f"🔎  Building dossier for: {name} ({domain})...")

    if not domain:
        print(f"  No domain provided for {name}")
        return None

    # Ensure domain has https://
    if not domain.startswith("http"):
        domain = f"https://{domain}"

    headers = {'User-Agent': 'Sentinel/1.0'}

    data = {
        "name": name,
        "domain": domain,
        "pricing_url": None,
        "pricing_verified": False,
        "careers_url": None,
        "careers_verified": False,
        "ats_url": None,
        "ats_type": None,
        "levelsfyi_slug": None,  # For companies without ATS
    }

    # Check if this company has known custom career page info
    custom_info = get_custom_careers_info(name)
    if custom_info:
        data["levelsfyi_slug"] = custom_info.get("levelsfyi_slug")
        if custom_info.get("careers_url"):
            data["careers_url"] = custom_info["careers_url"]
            data["careers_verified"] = True
            print(f"  ✓ Known custom careers: {data['careers_url']}")
        if custom_info.get("pricing_url"):
            data["pricing_url"] = custom_info["pricing_url"]
            data["pricing_verified"] = True
            print(f"  ✓ Known pricing URL: {data['pricing_url']}")

    # Try common pricing page paths (skip if already set from custom info)
    if not data["pricing_verified"]:
        # Extended list of common pricing paths
        pricing_paths = [
            "/pricing",
            "/pricing/",
            "/plans",
            "/plans/",
            "/plans-pricing",
            "/product/pricing",
            "/products/pricing",
            "/price",
            "/prices",
            "/packages",
            "/subscribe",
            "/buy",
        ]
        for path in pricing_paths:
            pricing_url = f"{domain.rstrip('/')}{path}"
            if verify_url(pricing_url, headers):
                data["pricing_url"] = pricing_url
                data["pricing_verified"] = True
                print(f"  ✓ Pricing: {pricing_url}")
                break

        # Fallback: Try to find pricing link from homepage navigation
        if not data["pricing_verified"]:
            print(f"  Searching homepage for pricing link...")
            pricing_url = _find_pricing_link_from_page(domain, headers)
            if pricing_url:
                data["pricing_url"] = pricing_url
                data["pricing_verified"] = True
                print(f"  ✓ Pricing (from nav): {pricing_url}")

        if not data["pricing_url"]:
            data["pricing_url"] = f"{domain.rstrip('/')}/pricing"
            print(f"  ? Pricing (unverified): {data['pricing_url']}")

    # Try common careers page paths (skip if already set from custom info)
    if not data["careers_verified"]:
        careers_paths = ["/jobs/all", "/careers",
                         "/jobs", "/about/careers", "/company/careers"]
        for path in careers_paths:
            careers_url = f"{domain.rstrip('/')}{path}"
            if verify_url(careers_url, headers):
                data["careers_url"] = careers_url
                data["careers_verified"] = True
                print(f"  ✓ Careers: {careers_url}")
                break

        if not data["careers_url"]:
            data["careers_url"] = f"{domain.rstrip('/')}/careers"
            print(f"  ? Careers (unverified): {data['careers_url']}")

    return data

# 3. The Eyes: Find the ATS (The Spider)


def extract_ats_from_careers(careers_url):
    """
    Visits a generic 'Careers' page and hunts for a link to a known ATS.
    Returns the first valid ATS URL found, or None.
    """
    print(f"Scouting {careers_url} for ATS links...")

    headers = {'User-Agent': 'Sentinel/1.0'}
    try:
        response = requests.get(careers_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, 'html.parser')
    except Exception as e:
        print(f"Failed to load careers page: {e}")
        return None

    # 1. Define the fingerprints of the ATS providers we support
    ats_patterns = [
        r"boards\.greenhouse\.io",
        r"jobs\.lever\.co",
        r"jobs\.ashbyhq\.com",
        r"myworkdayjobs\.com",
        r"breezy\.hr"
    ]

    # 2. Scan all <a> tags for these patterns
    for link in soup.find_all('a', href=True):
        href = link['href']

        # Check if any pattern exists in this link
        for pattern in ats_patterns:
            if re.search(pattern, href):
                print(f"ATS Found: {href}")
                return href

    print("No direct ATS link found (might be embedded or custom).")
    return None

# --- ORCHESTRATOR ---


def run_discovery(user_input):
    print("🧠 Asking Gemini for competitors...")
    competitors = suggest_competitors(user_input, num_competitors=1)

    if not competitors:
        print("No competitors found.")
        return []

    print(
        f"Found {len(competitors)} competitors: {[c.get('name') for c in competitors]}\n")

    full_dossiers = []

    for comp in competitors:
        links = find_company_links(comp)
        if links:
            # Use ghost_probe's detect_ats for better ATS detection
            if links["careers_url"]:
                print(f"🕷️  Scanning {links['careers_url']} for ATS...")
                ats_result = detect_ats(links["careers_url"])

                # Fallback: try common ATS URL patterns directly
                if not ats_result:
                    print(f"  Trying common ATS URL patterns...")
                    ats_result = try_common_ats_urls(links["name"])

                if ats_result:
                    # Clean up embed/version params from URL
                    clean_url = ats_result.get("url", "").split("?")[
                        0].split("/embed")[0]
                    links["ats_url"] = clean_url
                    links["ats_type"] = ats_result.get("type")
                    print(
                        f"  ✓ Found {ats_result.get('type').upper()}: {clean_url}")
                else:
                    print(f"  ✗ No ATS detected (may use custom system)")
            full_dossiers.append(links)
            time.sleep(1)  # Be polite

    return full_dossiers


if __name__ == "__main__":
    results = run_discovery(
        "Project management software for high performance teams")
    import json
    print(json.dumps(results, indent=2))
