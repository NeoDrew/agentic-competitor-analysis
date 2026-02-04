import os
import json
import asyncio
import datetime
import urllib.parse
import requests
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
load_dotenv(override=True)



# --- Helper to load prompt text from files ---
def _load_prompt_text(filepath: str) -> str:
    """Reads the prompt content from a file."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        if "state" in filepath:
            return "Analyze this text and extract key strategic data as JSON."
        return "Compare these two JSON objects and find the strategic difference."
    except Exception as e:
        return f"Error loading prompt: {e}"


# --- Step 1: Normalize Raw Data (Async) ---
async def _analyze_single_state(client: genai.Client, model_id: str, markdown_text: str | None, label: str, prompt_file: str) -> dict:
    if not markdown_text:
        return {"error": f"No data available for {label} state", "available": False}

    # Load the specific prompt for state extraction
    system_instruction = _load_prompt_text(prompt_file)

    # We inject the raw data into the user message
    user_payload = f"""
    === RAW DATA ({label.upper()}) ===
    {markdown_text[:20000]} 
    """  # 20k chars context window safety

    try:
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=user_payload,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        return {"error": f"Failed to analyze {label}: {str(e)}"}


# --- Step 2: Synthesize the Diff (Async) ---
async def _synthesize_diff(client: genai.Client, model_id: str, old_state: dict, new_state: dict, prompt_file: str) -> dict:
    # Load the specific prompt for comparison
    system_instruction = _load_prompt_text(prompt_file)

    user_payload = f"""
    === PREVIOUS STATE (JSON) ===
    {json.dumps(old_state, indent=2)}

    === CURRENT STATE (JSON) ===
    {json.dumps(new_state, indent=2)}
    """

    try:
        response = await client.aio.models.generate_content(
            model=model_id,
            contents=user_payload,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json"
            )
        )
        return json.loads(response.text)
    except Exception as e:
        return {"error": f"Diff failed: {str(e)}"}


# --- Scraping Helpers (Existing logic preserved) ---

async def _fetch_with_crawl4ai(url: str) -> str | None:
    try:
        if hasattr(crawl4ai, "scrape"):
            return await crawl4ai.scrape(url)
        if hasattr(crawl4ai, "crawl"):
            return await crawl4ai.crawl(url)
        if hasattr(crawl4ai, "Client"):
            client = crawl4ai.Client()
            if hasattr(client, "fetch"):
                return await client.fetch(url)
    except Exception:
        return None
    return None


async def _fetch_with_aiohttp(url: str) -> str:
    if not aiohttp:
        # Fallback to synchronous requests if aiohttp missing (not ideal but safe)
        return requests.get(url, headers={"User-Agent": "sentinel/1.0"}).text

    headers = {"User-Agent": "sentinel-probe/1.0"}
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()


async def get_current_state(url: str) -> str:
    html = None
    if crawl4ai:
        try:
            html = await _fetch_with_crawl4ai(url)
        except Exception:
            html = None
    if not html:
        html = await _fetch_with_aiohttp(url)
    md = mdify(html, heading_style="ATX")
    return md


def _timestamp_months_ago(months: int) -> str:
    now = datetime.datetime.utcnow()
    approx = now - datetime.timedelta(days=30 * months)
    return approx.strftime("%Y%m%d")


def get_historical_state(url: str, months_ago: int) -> tuple[str | None, str | None]:
    # This remains synchronous because 'requests' and 'waybackpy' are sync.
    # The API wrapper handles this in a thread executor.
    timestamp = _timestamp_months_ago(months_ago)
    ua = "sentinel-probe/1.0"

    # 1. Try Waybackpy wrapper
    if waybackpy:
        try:
            from waybackpy import WaybackMachineAvailabilityAPI
            api = WaybackMachineAvailabilityAPI(url, ua)
            closest = api.near(year=int(timestamp[:4]), month=int(
                timestamp[4:6]), day=int(timestamp[6:8]))
            if closest and closest.archive_url:
                r = requests.get(closest.archive_url, headers={
                                 "User-Agent": ua}, timeout=30)
                return mdify(r.text, heading_style="ATX"), closest.archive_url
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
        r = requests.get(api_url, headers={"User-Agent": ua}, timeout=30)
        j = r.json()
        snap = j.get("archived_snapshots", {}).get("closest")
        if not snap:
            return None, None
        snapshot_url = snap.get("url")
        r2 = requests.get(snapshot_url, headers={"User-Agent": ua}, timeout=30)
        return mdify(r2.text, heading_style="ATX"), snapshot_url
    except Exception:
        return None, None


# --- Main Orchestrator ---

async def analyze_diff(old_md: str | None, new_md: str | None,
                       state_prompt_path: str = "prompt_state.yaml",
                       diff_prompt_path: str = "prompt_diff.yaml") -> dict:

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    print(
        f"--- Sentinel: Reading prompts from {state_prompt_path} and {diff_prompt_path} ---")

    # 1. Run State Analysis in Parallel
    print("--- Sentinel: Analyzing States ---")
    task_old = _analyze_single_state(
        client, model_id, old_md, "historical", state_prompt_path)
    task_new = _analyze_single_state(
        client, model_id, new_md, "current", state_prompt_path)

    results = await asyncio.gather(task_old, task_new)
    old_struct, new_struct = results

    # 2. Run Strategy Diff
    print("--- Sentinel: Synthesizing Diff ---")
    diff_struct = await _synthesize_diff(client, model_id, old_struct, new_struct, diff_prompt_path)

    return {
        "timestamp": datetime.datetime.utcnow().isoformat(),
        "old_state": old_struct,
        "new_state": new_struct,
        "analysis": diff_struct
    }
