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

# --- Helper: Save Prompt to File (Restored) ---
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
async def _call_gemini_with_retry(client, model_id, contents, system_instruction, retries=3):
    is_gemma = "gemma" in model_id.lower()
    config_params = {"system_instruction": system_instruction}
    if not is_gemma:
        config_params["response_mime_type"] = "application/json"
    config = types.GenerateContentConfig(**config_params)

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
            if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                wait_time = (2 ** attempt) * 2
                print(f"⚠️  Quota limit hit. Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                print(f"❌ API Error: {error_str}")
                return {"error": error_str}
    return {"error": "Max retries exceeded."}

# --- NEW: Smart HTML Cleaner ---
def _clean_html(html_content: str) -> str:
    if not html_content:
        return ""
    
    soup = BeautifulSoup(html_content, "html.parser")
    
    # Kill Scripts, Styles, etc.
    for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Kill Comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # Kill Navigation & Footers
    for tag in soup(["nav", "footer", "header", "aside"]):
        tag.decompose()

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
    _save_prompt_to_file(f"state_{label}", log_content)

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
    _save_prompt_to_file("diff", log_content)

    return await _call_gemini_with_retry(client, model_id, user_payload, system_instruction)

# --- Scraping Helpers (Fixed User-Agent) ---
async def _fetch_with_aiohttp(url: str) -> str:
    headers = {"User-Agent": "sentinel-probe/1.0"} 
    if not aiohttp:
        return requests.get(url, headers=headers).text
    
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.text()

async def get_current_state(url: str) -> str:
    html = await _fetch_with_aiohttp(url)
    clean_html = _clean_html(html)
    return mdify(clean_html, heading_style="ATX", strip=['img'])

def get_historical_state(url: str, months_ago: int) -> tuple[str | None, str | None]:
    timestamp = (datetime.datetime.utcnow() - datetime.timedelta(days=30 * months_ago)).strftime("%Y%m%d")
    ua = "sentinel-probe/1.0"
    
    # 1. Try Waybackpy
    if waybackpy:
        try:
            from waybackpy import WaybackMachineAvailabilityAPI
            api = WaybackMachineAvailabilityAPI(url, ua)
            closest = api.near(year=int(timestamp[:4]), month=int(timestamp[4:6]), day=int(timestamp[6:8]))
            if closest and closest.archive_url:
                r = requests.get(closest.archive_url, headers={"User-Agent": ua}, timeout=30)
                clean_html = _clean_html(r.text)
                return mdify(clean_html, heading_style="ATX", strip=['img']), closest.archive_url
        except Exception:
            pass

    # 2. Try Raw API
    api_url = (
        "[https://archive.org/wayback/available?url=](https://archive.org/wayback/available?url=)"
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
        clean_html = _clean_html(r2.text)
        return mdify(clean_html, heading_style="ATX", strip=['img']), snapshot_url
    except Exception:
        return None, None

# --- Main Orchestrator ---
async def analyze_diff(old_md: str | None, new_md: str | None, 
                       state_prompt_path: str = "prompt_state.yaml", 
                       diff_prompt_path: str = "prompt_diff.yaml",
                       target_url: str = "unknown") -> dict: # Added target_url arg
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    client = genai.Client(api_key=api_key)
    # Use 1.5 Pro (Stable/High Detail) or 2.0 Pro Exp (Newer)
    model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

    print(f"--- Sentinel: Using Model {model_id} ---")

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

    # --- SAVE OUTPUT ---
    _save_output_to_file(final_result, target_url)

    return final_result