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


def try_common_ats_urls(company_name: str) -> dict | None:
    """
    Fallback: directly check if common ATS URLs exist for a company.
    Many companies have predictable ATS URLs even if not linked from careers page.
    """
    # Normalize company name for URL (lowercase, remove spaces/punctuation)
    slug = company_name.lower().replace(" ", "").replace(".", "").replace("-", "")
    slug_hyphen = company_name.lower().replace(" ", "-").replace(".", "")

    ats_patterns = [
        ("greenhouse", f"https://boards.greenhouse.io/{slug}"),
        ("greenhouse", f"https://boards.greenhouse.io/{slug_hyphen}"),
        ("lever", f"https://jobs.lever.co/{slug}"),
        ("lever", f"https://jobs.lever.co/{slug_hyphen}"),
        ("ashby", f"https://jobs.ashbyhq.com/{slug}"),
        ("ashby", f"https://jobs.ashbyhq.com/{slug_hyphen}"),
        ("ashby", f"https://jobs.ashbyhq.com/{company_name}"),
    ]

    headers = {'User-Agent': 'Sentinel/1.0'}

    for ats_type, url in ats_patterns:
        try:
            resp = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
            if resp.status_code == 200:
                return {"type": ats_type, "url": url}
        except requests.RequestException:
            continue

    return None

load_dotenv()


def suggest_competitors(user_description: str, num_competitors: int = 5) -> list[dict]:
    """
    Uses Gemini to suggest competitors based on a product/company description.

    Args:
        user_description: Description of the product/company to find competitors for
        num_competitors: Number of competitors to return (default 5)

    Returns:
        List of dicts with 'name' and 'domain' keys
    """
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
Example output: [{"name": "Asana", "domain": "asana.com"}, {"name": "Linear", "domain": "linear.app"}]"""

    user_prompt = f"""Identify {num_competitors} direct competitors for the following:

{user_description}

Return a JSON array with name and domain for each competitor."""

    try:
        config = types.GenerateContentConfig(
            system_instruction=system_instruction,
            response_mime_type="application/json"
        )

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
        print(f"Gemini API error: {e}")
        return []

# 2. The Hands: Find the URLs


def verify_url(url: str, headers: dict) -> bool:
    """Check if a URL returns 200 OK."""
    try:
        resp = requests.head(url, headers=headers, timeout=5, allow_redirects=True)
        return resp.status_code == 200
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
        "ats_type": None
    }

    # Try common pricing page paths
    pricing_paths = ["/pricing", "/plans", "/plans-pricing", "/pricing/", "/product/pricing"]
    for path in pricing_paths:
        pricing_url = f"{domain.rstrip('/')}{path}"
        if verify_url(pricing_url, headers):
            data["pricing_url"] = pricing_url
            data["pricing_verified"] = True
            print(f"  ✓ Pricing: {pricing_url}")
            break

    if not data["pricing_url"]:
        data["pricing_url"] = f"{domain.rstrip('/')}/pricing"
        print(f"  ? Pricing (unverified): {data['pricing_url']}")

    # Try common careers page paths
    careers_paths = ["/careers", "/jobs", "/about/careers", "/company/careers"]
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
    competitors = suggest_competitors(user_input)

    if not competitors:
        print("No competitors found.")
        return []

    print(f"Found {len(competitors)} competitors: {[c.get('name') for c in competitors]}\n")

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
                    clean_url = ats_result.get("url", "").split("?")[0].split("/embed")[0]
                    links["ats_url"] = clean_url
                    links["ats_type"] = ats_result.get("type")
                    print(f"  ✓ Found {ats_result.get('type').upper()}: {clean_url}")
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
