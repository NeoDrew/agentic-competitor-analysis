import os
import json
import asyncio
import re
import datetime
import urllib.parse
import requests
from bs4 import BeautifulSoup, Comment
from google import genai
from google.genai import types
from markdownify import markdownify as mdify

try:
    import crawl4ai
except Exception:
    crawl4ai = None

try:
    import waybackpy
except Exception:
    waybackpy = None
    
try:
    import aiohttp
except Exception:
    aiohttp = None
    
from dotenv import load_dotenv
load_dotenv()

# --- Helper: Save Output to JSON File (NEW) ---
def _save_output_to_file(data: dict, url: str):
    """
    Saves the final analysis result to a JSON file in 'reports/'.
    """
    try:
        os.makedirs("reports", exist_ok=True)
        
        # Create a safe filename from the URL
        safe_name = urllib.parse.urlparse(url).netloc.replace(".", "-")
        timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
        filename = f"reports/diff_{safe_name}_{timestamp}.json"
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"✅ Analysis saved to: {filename}")
        return filename
    except Exception as e:
        print(f"⚠️  Warning: Failed to save output file: {e}")
        return None

# --- Helper: Save Prompt to File ---
def _save_prompt_to_file(label: str, content: str):
    """
    Saves the full prompt context to a file for debugging.
    """
    try:
        os.makedirs("saved_prompts", exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%SZ")
        # specific hash to avoid overwrites if multiple calls happen fast
        filename = f"saved_prompts/{timestamp}_{label}_{abs(hash(content)) % 100000000}.txt"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(content)
        return filename
    except Exception as e:
        print(f"Warning: Failed to save prompt log: {e}")
        return None

# --- Helper: Load Prompt Text ---
def _load_prompt_text(filepath: str) -> str:
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        if "state" in filepath:
            return "Analyze this text and extract key strategic data as JSON."
        return "Compare these two JSON objects and find the strategic difference."
    except Exception as e:
        return f"Error loading prompt: {e}"

# --- Helper: Clean JSON Output ---
def _clean_json_text(text: str) -> str:
    """
    Robust cleaning: Strips Markdown AND finds the first/last brace
    to extract valid JSON from chatty LLM responses.
    """
    if not text:
        return "{}"
    
    # 1. Strip Markdown code blocks
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"```$", "", text, flags=re.MULTILINE)
    
    # 2. Find the JSON object (first '{' to last '}')
    start = text.find('{')
    end = text.rfind('}')
    
    if start != -1 and end != -1:
        text = text[start:end+1]
    
    return text.strip()

# --- Helper: Auto-Retry & Config Manager ---
async def _call_gemini_with_retry(client, model_id, contents, system_instruction, retries=5):
    """
    Call Gemini API with exponential backoff retry for transient errors.
    Handles 429 (rate limit), 503 (overloaded), and other transient errors.
    """
    is_gemma = "gemma" in model_id.lower()
    config_params = {"system_instruction": system_instruction}
    if not is_gemma:
        config_params["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**config_params)

    # Transient error codes that should trigger retry
    retryable_errors = ['429', '503', 'RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'overloaded']

    for attempt in range(retries):
        try:
            response = await client.aio.models.generate_content(
                model=model_id,
                contents=contents,
                config=config
            )
            clean_text = _clean_json_text(response.text)
            try:
                return json.loads(clean_text)
            except json.JSONDecodeError:
                return {"error": "JSON Parse Failed", "raw_text": clean_text[:500]}
        except Exception as e:
            error_str = str(e)
            is_retryable = any(code in error_str for code in retryable_errors)

            if is_retryable and attempt < retries - 1:
                # Exponential backoff: 2s, 4s, 8s, 16s, 32s
                wait_time = (2 ** attempt) * 2
                print(f"⚠️  API overloaded (attempt {attempt + 1}/{retries}). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                print(f"❌ API Error: {error_str}")
                return {"error": error_str}

    return {"error": "Max retries exceeded after backoff."}

# --- NEW: Smart HTML Cleaner ---
def _clean_html(html_content: str) -> str:
    if not html_content:
        return ""

    soup = BeautifulSoup(html_content, "html.parser")

    # Kill Scripts, Styles, etc.
    for tag in soup(["script", "style", "noscript", "iframe"]):
        tag.decompose()

    # Kill Comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Try to find main content area first (more targeted extraction)
    main_content = soup.find("main") or soup.find("article") or soup.find(id="content")

    if main_content:
        # Only clean navigation within main content
        for tag in main_content.find_all(["nav"]):
            tag.decompose()
        return str(main_content)

    # Fallback: clean whole page but be less aggressive
    # Only remove top-level nav/footer (direct children of body)
    body = soup.find("body")
    if body:
        for tag in body.find_all(["nav", "footer", "header"], recursive=False):
            tag.decompose()
        # Also remove common navigation divs
        for div in body.find_all("div", recursive=False):
            div_class = " ".join(div.get("class", []))
            div_id = div.get("id", "")
            if any(nav_term in (div_class + div_id).lower() for nav_term in ["nav", "header", "footer", "menu"]):
                div.decompose()
        return str(body)

    return str(soup)

# --- Analysis Step (With Logging) ---
async def _analyze_single_state(client, model_id, markdown_text, label, prompt_file):
    if not markdown_text:
        return {"error": f"No data available for {label} state"}

    system_instruction = _load_prompt_text(prompt_file)
    
    # Smart Truncation
    if len(markdown_text) > 25000:
        cleaned_md = markdown_text[:15000] + "\n\n...[TRUNCATED MIDDLE]...\n\n" + markdown_text[-5000:]
    else:
        cleaned_md = markdown_text

    user_payload = f"=== RAW DATA ({label.upper()}) ===\n{cleaned_md}" 

    # LOGGING RESTORED: Save the inputs to a file
    log_content = f"SYSTEM_INSTRUCTION:\n{system_instruction}\n\nUSER_PAYLOAD:\n{user_payload}"
    # _save_prompt_to_file(f"state_{label}", log_content)

    return await _call_gemini_with_retry(client, model_id, user_payload, system_instruction)

# --- Step 2: Synthesize Diff (With Logging) ---
async def _synthesize_diff(client, model_id, old_state, new_state, prompt_file):
    if "error" in old_state or "error" in new_state:
        return {
            "error": "Comparison aborted due to failure in state analysis.", 
            "details": {"old": old_state.get("error"), "new": new_state.get("error")}
        }

    system_instruction = _load_prompt_text(prompt_file)
    user_payload = f"""
    === PREVIOUS STATE (JSON) ===
    {json.dumps(old_state, indent=2)}

    === CURRENT STATE (JSON) ===
    {json.dumps(new_state, indent=2)}
    """
    
    # LOGGING RESTORED
    log_content = f"SYSTEM_INSTRUCTION:\n{system_instruction}\n\nUSER_PAYLOAD:\n{user_payload}"
    # _save_prompt_to_file("diff", log_content)

    return await _call_gemini_with_retry(client, model_id, user_payload, system_instruction)

# --- Scraping Helpers (Improved Headers & Retries) ---
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    # Don't request compression - simpler to handle uncompressed responses
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


async def _fetch_with_aiohttp(url: str, retries: int = 3) -> str:
    """Fetch URL content with browser-like headers and retry logic."""
    last_error = None

    for attempt in range(retries):
        try:
            if not aiohttp:
                resp = requests.get(url, headers=BROWSER_HEADERS, timeout=30, allow_redirects=True)
                resp.raise_for_status()
                return resp.text

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(headers=BROWSER_HEADERS, timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    resp.raise_for_status()
                    return await resp.text()
        except Exception as e:
            last_error = e
            if attempt < retries - 1:
                await asyncio.sleep(2 ** attempt)  # Exponential backoff

    raise last_error or Exception("Failed to fetch URL")


async def get_current_state(url: str) -> str:
    """
    Fetch and convert a URL to markdown for analysis.
    Includes retry logic and special handling for problematic sites.
    Falls back to Wayback Machine for JS-heavy pages.
    """
    try:
        html = await _fetch_with_aiohttp(url)

        # Check if we got meaningful content
        if not html or len(html.strip()) < 500:
            print(f"    ⚠ Page returned minimal content ({len(html) if html else 0} chars)")
            return ""

        clean_html = _clean_html(html)
        markdown = mdify(clean_html, heading_style="ATX", strip=['img'])

        # Validate we got something useful
        if not markdown or len(markdown.strip()) < 100:
            # Page might be JS-rendered - try Wayback Machine as fallback
            print(f"    ⚠ Page appears to be JS-rendered, trying Wayback Machine...")
            wayback_md, _ = get_historical_state(url, months_ago=0)  # Get most recent snapshot
            if wayback_md and len(wayback_md.strip()) > 100:
                print(f"    ✓ Using Wayback Machine snapshot")
                return wayback_md
            print(f"    ⚠ Could not get content (JS-rendered page without cached version)")
            return ""

        return markdown
    except Exception as e:
        print(f"    ✗ Failed to fetch {url}: {e}")
        return ""

def get_historical_state(url: str, months_ago: int) -> tuple[str | None, str | None]:
    timestamp = (datetime.datetime.utcnow() - datetime.timedelta(days=30 * months_ago)).strftime("%Y%m%d")

    # 1. Try Waybackpy
    if waybackpy:
        try:
            from waybackpy import WaybackMachineAvailabilityAPI
            api = WaybackMachineAvailabilityAPI(url, BROWSER_HEADERS.get("User-Agent"))
            closest = api.near(year=int(timestamp[:4]), month=int(timestamp[4:6]), day=int(timestamp[6:8]))
            if closest and closest.archive_url:
                r = requests.get(closest.archive_url, headers=BROWSER_HEADERS, timeout=30)
                clean_html = _clean_html(r.text)
                return mdify(clean_html, heading_style="ATX", strip=['img']), closest.archive_url
        except Exception:
            pass

    # 2. Try Raw API
    api_url = (
        "https://archive.org/wayback/available?url="
        + urllib.parse.quote(url, safe="")
        + "&timestamp="
        + timestamp
    )
    try:
        r = requests.get(api_url, headers=BROWSER_HEADERS, timeout=30)
        j = r.json()
        snap = j.get("archived_snapshots", {}).get("closest")
        if not snap:
            return None, None

        snapshot_url = snap.get("url")
        r2 = requests.get(snapshot_url, headers=BROWSER_HEADERS, timeout=30)
        clean_html = _clean_html(r2.text)
        return mdify(clean_html, heading_style="ATX", strip=['img']), snapshot_url
    except Exception:
        return None, None

# --- Main Orchestrator ---
async def analyze_diff(old_md: str | None, new_md: str | None,
                       state_prompt_path: str = "prompt_state.yaml",
                       diff_prompt_path: str = "prompt_diff.yaml",
                       target_url: str = "unknown") -> dict:
    """
    Analyze pricing page data. Supports two modes:
    1. Full diff: When both old_md and new_md are provided
    2. Current-only: When only new_md is provided (no historical data)
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

    print(f"--- Sentinel: Using Model {model_id} ---")

    # Handle current-only mode (no historical data)
    if not old_md and new_md:
        print("--- Sentinel: Analyzing Current State Only ---")
        new_struct = await _analyze_single_state(client, model_id, new_md, "current", state_prompt_path)

        final_result = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "url": target_url,
            "old_state": None,
            "new_state": new_struct,
            "analysis": {
                "change_detected": False,
                "strategic_shift": "Current state only - no historical comparison available",
                "evidence": {}
            }
        }
        return final_result

    # Full diff mode
    print("--- Sentinel: Analyzing States ---")
    task_old = _analyze_single_state(client, model_id, old_md, "historical", state_prompt_path)
    task_new = _analyze_single_state(client, model_id, new_md, "current", state_prompt_path)

    old_struct, new_struct = await asyncio.gather(task_old, task_new)

    print("--- Sentinel: Synthesizing Diff ---")
    diff_struct = await _synthesize_diff(client, model_id, old_struct, new_struct, diff_prompt_path)

    final_result = {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "url": target_url,
        "old_state": old_struct,
        "new_state": new_struct,
        "analysis": diff_struct
    }

    return final_result