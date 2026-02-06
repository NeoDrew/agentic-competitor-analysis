"""
Spy Report - Homepage Intelligence Module

Analyzes competitor homepages for messaging, positioning, and strategic changes.
Compares current state with historical snapshots via Wayback Machine.
"""
import os
import json
import asyncio
import datetime

from dotenv import load_dotenv
load_dotenv()

# Import shared utilities from sentinel_probe
from sentinel_probe import (
    get_current_state,
    get_historical_state,
    _call_gemini_with_retry,
    _load_prompt_text,
)

from google import genai


async def _analyze_homepage_state(client, model_id, markdown_text: str, label: str) -> dict:
    """
    Analyze a homepage snapshot and extract structured positioning data.
    """
    if not markdown_text:
        return {"error": f"No data available for {label} state"}

    prompt_file = "prompt_homepage_state.yaml"
    system_instruction = _load_prompt_text(prompt_file)

    # Smart Truncation - homepages can be large
    if len(markdown_text) > 30000:
        cleaned_md = markdown_text[:20000] + "\n\n...[TRUNCATED]...\n\n" + markdown_text[-5000:]
    else:
        cleaned_md = markdown_text

    user_payload = f"=== HOMEPAGE CONTENT ({label.upper()}) ===\n{cleaned_md}"

    return await _call_gemini_with_retry(client, model_id, user_payload, system_instruction)


async def _synthesize_homepage_diff(client, model_id, old_state: dict, new_state: dict) -> dict:
    """
    Compare two homepage states and identify strategic changes.
    """
    if "error" in old_state or "error" in new_state:
        return {
            "error": "Comparison aborted due to failure in state analysis.",
            "details": {"old": old_state.get("error"), "new": new_state.get("error")}
        }

    prompt_file = "prompt_homepage_diff.yaml"
    system_instruction = _load_prompt_text(prompt_file)

    user_payload = f"""
    === PREVIOUS HOMEPAGE STATE (JSON) ===
    {json.dumps(old_state, indent=2)}

    === CURRENT HOMEPAGE STATE (JSON) ===
    {json.dumps(new_state, indent=2)}
    """

    return await _call_gemini_with_retry(client, model_id, user_payload, system_instruction)


async def analyze_homepage(homepage_url: str, months_ago: int = 6) -> dict:
    """
    Main entry point for homepage analysis.

    Args:
        homepage_url: The company's homepage URL
        months_ago: How many months back to compare (default: 6)

    Returns:
        Dict with old_state, new_state, and analysis
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-pro-latest")

    print(f"--- Spy Report: Analyzing {homepage_url} ---")

    # Get current homepage
    print("  Fetching current homepage...")
    current_md = await get_current_state(homepage_url)

    if not current_md or len(current_md.strip()) < 100:
        return {
            "error": "Could not fetch homepage content",
            "url": homepage_url,
            "timestamp": datetime.datetime.utcnow().isoformat()
        }

    # Get historical homepage
    print(f"  Fetching historical snapshot (~{months_ago} months ago)...")
    old_md, snapshot_url = get_historical_state(homepage_url, months_ago)

    # Analyze states
    if old_md and current_md:
        print("  Analyzing both states...")
        task_old = _analyze_homepage_state(client, model_id, old_md, "historical")
        task_new = _analyze_homepage_state(client, model_id, current_md, "current")

        old_struct, new_struct = await asyncio.gather(task_old, task_new)

        print("  Synthesizing changes...")
        diff_struct = await _synthesize_homepage_diff(client, model_id, old_struct, new_struct)

        return {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "url": homepage_url,
            "snapshot_url": snapshot_url,
            "old_state": old_struct,
            "new_state": new_struct,
            "analysis": diff_struct
        }
    else:
        # No historical data - analyze current only
        print("  No historical snapshot available, analyzing current only...")
        new_struct = await _analyze_homepage_state(client, model_id, current_md, "current")

        return {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "url": homepage_url,
            "snapshot_url": None,
            "old_state": None,
            "new_state": new_struct,
            "analysis": {
                "change_detected": False,
                "strategic_shift": "Current state only - no historical comparison available",
                "evidence": {}
            }
        }


async def main():
    """CLI for testing homepage analysis."""
    import sys

    if len(sys.argv) < 2:
        print("Usage: python spy_report.py <homepage_url> [months_ago]")
        print("Example: python spy_report.py https://linear.app 6")
        sys.exit(1)

    url = sys.argv[1]
    months = int(sys.argv[2]) if len(sys.argv) > 2 else 6

    result = await analyze_homepage(url, months)
    print("\n" + "="*60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
