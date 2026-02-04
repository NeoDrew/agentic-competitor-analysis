#!/usr/bin/env python3
"""Generate a Sentinel PDF report for a queued job.

Usage: python3 generate_report.py --url https://linear.app/pricing

This script POSTs to the local API (/analyze), polls /jobs/{job_id} until completion,
then creates a PDF using `fpdf` with the Verdict and an Evidence table.
"""
import argparse
import json
import os
import time
import urllib.parse
from datetime import datetime

import requests

try:
    from fpdf import FPDF
except Exception:
    FPDF = None


def pretty_domain(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
        return p.netloc or url
    except Exception:
        return url


def sanitize_text(text: str) -> str:
    """
    Replaces incompatible Unicode characters with ASCII equivalents 
    to prevent FPDF Latin-1 encoding errors.
    """
    if not isinstance(text, str):
        return str(text)
    
    replacements = {
        "\u2018": "'",  # Left single quote
        "\u2019": "'",  # Right single quote
        "\u201c": '"',  # Left double quote
        "\u201d": '"',  # Right double quote
        "\u2013": "-",  # En dash
        "\u2014": "--", # Em dash
        "\u2026": "...", # Ellipsis
        "\u2022": "*",   # Bullet
        "\u00A0": " ",   # Non-breaking space
    }
    
    for char, replacement in replacements.items():
        text = text.replace(char, replacement)
        
    # Final safety net: encode to latin-1, replace errors with '?'
    return text.encode('latin-1', 'replace').decode('latin-1')


def format_state_object(state_obj: dict) -> str:
    """Helper to turn a State JSON object into a readable string for the PDF."""
    if not state_obj or not isinstance(state_obj, dict):
        return "No data available."
    
    lines = []
    
    # Extract key fields common in your prompt_state.yaml
    tagline = state_obj.get("tagline", "N/A")
    lines.append(f"TAGLINE: {tagline}\n")
    
    audience = state_obj.get("target_audience", "N/A")
    lines.append(f"AUDIENCE: {audience}\n")
    
    # Handle lists (like features or pricing tiers)
    pricing = state_obj.get("pricing_tiers", [])
    if isinstance(pricing, list):
        lines.append("PRICING:")
        for p in pricing[:5]: # Limit to 5 to save space
            lines.append(f"- {p}")
    elif isinstance(pricing, str):
        lines.append(f"PRICING: {pricing}")
        
    lines.append("") # Spacer
    
    val_prop = state_obj.get("core_value_prop")
    if val_prop:
         lines.append(f"VALUE PROP: {val_prop}")

    return "\n".join(lines)


def extract_evidence(result: dict) -> tuple[str, str, str]:
    """
    Parses the new API response structure.
    """
    # 1. Extract the Analysis/Verdict
    analysis = result.get("analysis", {})
    
    # Fallback if analysis is just a string (old error format)
    if isinstance(analysis, str):
        return "Analysis Error", analysis, ""

    # Get the "So What?"
    verdict = analysis.get("strategic_analysis") or \
              analysis.get("summary_of_changes") or \
              analysis.get("strategic_shift") or \
              "No strategic shift detected."
    
    # If the boolean flag says false, override verdict title
    if analysis.get("change_detected") is False:
        verdict = "NO STRATEGIC SHIFT DETECTED\n(Site content appears stable)"

    # 2. Format the columns
    old_data = result.get("old_state", {})
    new_data = result.get("new_state", {})
    
    left_text = format_state_object(old_data)
    right_text = format_state_object(new_data)

    return verdict, left_text, right_text


def poll_job(api_base: str, job_id: str, timeout: int = 300, interval: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{api_base}/jobs/{job_id}")
            if r.status_code == 200:
                j = r.json()
                status = j.get("status")
                if status in ("completed", "failed"):
                    return j
            else:
                print(f"Warning: API returned {r.status_code}")
        except requests.exceptions.ConnectionError:
            print("Warning: Connection to API failed. Retrying...")
            
        time.sleep(interval)
    raise SystemExit("Timed out waiting for job to complete")


def make_pdf(outfile: str, competitor: str, verdict: str, left: str, right: str):
    if FPDF is None:
        raise SystemExit(
            "fpdf not installed. Run: python3 -m pip install fpdf")

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()

    # --- Header ---
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, sanitize_text(f"Sentinel Strategic Alert: {competitor}"), ln=True)
    pdf.ln(2)

    # --- Verdict Section ---
    pdf.set_font("Helvetica", "B", 12) # Reduced slightly to fit long text
    pdf.set_text_color(220, 30, 30) # Red for impact
    
    # Sanitize verdict text
    clean_verdict = sanitize_text(verdict)
    pdf.multi_cell(0, 6, clean_verdict)
    pdf.ln(6)
    pdf.set_text_color(0, 0, 0) # Reset to black

    # --- Comparison Table Setup ---
    pdf.set_font("Helvetica", "B", 12)
    # Calculate column widths
    page_w = pdf.w - 2 * pdf.l_margin
    col_w = (page_w / 2) - 4 # Minus padding
    
    # Table Headers
    # Save Y position
    y_header = pdf.get_y()
    
    pdf.cell(col_w, 10, "6 Months Ago (Historical)", border=1, align='C')
    # Move to right column
    pdf.set_xy(pdf.l_margin + col_w + 4, y_header)
    pdf.cell(col_w, 10, "Today (Current)", border=1, align='C')
    pdf.ln(12)

    # --- Column Content ---
    pdf.set_font("Helvetica", "", 10)
    
    y_start_content = pdf.get_y()
    
    # Left Column (Historical)
    clean_left = sanitize_text(left)
    pdf.set_xy(pdf.l_margin, y_start_content)
    pdf.multi_cell(col_w, 6, clean_left, border=0)
    y_end_left = pdf.get_y()
    
    # Right Column (Current)
    clean_right = sanitize_text(right)
    pdf.set_xy(pdf.l_margin + col_w + 4, y_start_content)
    pdf.multi_cell(col_w, 6, clean_right, border=0)
    y_end_right = pdf.get_y()
    
    # --- Footer / Metadata ---
    # Move to whichever column ended lower
    y_final = max(y_end_left, y_end_right) + 10
    
    # Check if we need a new page
    if y_final > 250:
        pdf.add_page()
        y_final = 20

    pdf.set_y(y_final)
    
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 10, "Generated by Sentinel AI | Confidentially Distributed", align='C')

    pdf.output(outfile)


def main():
    parser = argparse.ArgumentParser(
        description="Queue analysis and generate PDF report")
    parser.add_argument("--url", required=True, help="Target competitor URL")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--api", default=os.getenv("SENTINEL_API",
                        "http://127.0.0.1:8000"), help="Base URL of Sentinel API")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    api_base = args.api.rstrip("/")

    # 1) Queue job
    print(f"Submitting job for {args.url}...")
    try:
        resp = requests.post(f"{api_base}/analyze",
                             json={"url": args.url, "months": args.months})
        if resp.status_code not in (200, 201):
            raise SystemExit(
                f"Failed to submit job: {resp.status_code} {resp.text}")
        job = resp.json()
    except requests.exceptions.ConnectionError:
         raise SystemExit(f"Could not connect to API at {api_base}. Is it running?")

    job_id = job.get("job_id")
    print(f"Job queued: {job_id}")

    # 2) Poll until done
    print("Waiting for analysis (this may take 30-60s)...")
    j = poll_job(api_base, job_id, timeout=args.timeout)
    
    if j.get("status") == "failed":
        print("\nJob Failed!")
        print(f"Reason: {j.get('error')}")
        return

    result = j.get("result")
    if not result:
        print("Job completed but returned no result data.")
        return

    # 3) Extract evidence and verdict
    verdict, left, right = extract_evidence(result)

    competitor = pretty_domain(j.get("url", args.url))
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    safe_name = competitor.replace(":", "_").replace("/", "_").replace(".", "-")
    out = f"Sentinel_{safe_name}_{ts}.pdf"

    print(f"\nGenerating Report: {out}")
    make_pdf(out, competitor, verdict, left, right)
    print("Success.")


if __name__ == "__main__":
    main()