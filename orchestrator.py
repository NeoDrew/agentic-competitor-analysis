#!/usr/bin/env python3
"""
Sentinel Orchestrator - Full Competitive Intelligence Pipeline

Usage:
    python orchestrator.py "Project management software for engineering teams"
    python orchestrator.py --competitors "Linear,Asana,ClickUp"
"""
import argparse
import asyncio
import json
import os
import time
from datetime import datetime

from google import genai
from google.genai import types

from discovery import suggest_competitors, find_company_links, try_common_ats_urls
from ghost_probe import (
    detect_ats, fetch_jobs, analyze_hiring_trends,
    fetch_jobs_from_levelsfyi, fetch_jobs_from_linkedin, fetch_jobs_direct_careers
)
from sentinel_probe import get_current_state, get_historical_state, analyze_diff
from background_probe import gather_company_background
from spy_report import analyze_homepage

# Directory for storing job snapshots (for future comparison)
SNAPSHOTS_DIR = "snapshots"
REPORTS_DIR = "reports"


def ensure_dirs():
    """Create necessary directories."""
    os.makedirs(SNAPSHOTS_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)


def get_snapshot_path(company_name: str) -> str:
    """Get the path for a company's job snapshot file."""
    safe_name = company_name.lower().replace(" ", "_").replace(".", "")
    return os.path.join(SNAPSHOTS_DIR, f"{safe_name}_jobs.json")


def load_previous_snapshot(company_name: str) -> list[dict] | None:
    """Load previous job snapshot if it exists."""
    path = get_snapshot_path(company_name)
    if os.path.exists(path):
        try:
            with open(path, 'r') as f:
                data = json.load(f)
                return data.get('jobs', [])
        except (json.JSONDecodeError, IOError):
            pass
    return None


def save_snapshot(company_name: str, jobs: list[dict], ats_url: str):
    """Save current jobs as snapshot for future comparison."""
    path = get_snapshot_path(company_name)
    data = {
        'company': company_name,
        'ats_url': ats_url,
        'timestamp': datetime.now().isoformat(),
        'job_count': len(jobs),
        'jobs': jobs
    }
    with open(path, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"  📸 Snapshot saved: {path}")


def analyze_jobs_with_ai(jobs: list[dict], company_name: str) -> dict:
    """
    Analyze current job listings to infer strategic direction.
    Returns insights about what the hiring patterns suggest.
    """
    if not jobs:
        return {"summary": "No job data available", "signals": []}

    # Count by department
    dept_counts = {}
    for job in jobs:
        dept = job.get('department', 'General')
        dept_counts[dept] = dept_counts.get(dept, 0) + 1

    # Look for strategic keywords
    keywords = {
        'AI/ML': ['ai', 'machine learning', 'ml', 'llm', 'gpt', 'neural'],
        'Enterprise': ['enterprise', 'b2b', 'sales', 'account executive'],
        'Platform': ['platform', 'infrastructure', 'devops', 'sre'],
        'Security': ['security', 'compliance', 'soc', 'privacy'],
        'Growth': ['growth', 'marketing', 'demand gen', 'content'],
        'International': ['emea', 'apac', 'international', 'remote'],
    }

    signals = []
    for category, terms in keywords.items():
        matches = sum(1 for job in jobs if any(
            term in job.get('title', '').lower() for term in terms
        ))
        if matches > 0:
            signals.append({
                'category': category,
                'count': matches,
                'percent': round(matches / len(jobs) * 100, 1)
            })

    # Sort by count
    signals.sort(key=lambda x: x['count'], reverse=True)

    # Top departments
    top_depts = sorted(dept_counts.items(), key=lambda x: x[1], reverse=True)[:5]

    return {
        'total_jobs': len(jobs),
        'top_departments': [{'name': d, 'count': c} for d, c in top_depts],
        'strategic_signals': signals[:5],
        'summary': _generate_hiring_summary(company_name, len(jobs), top_depts, signals)
    }


def _generate_hiring_summary(company: str, total: int, depts: list, signals: list) -> str:
    """Generate a human-readable hiring summary."""
    lines = [f"{company} has {total} open positions."]

    if depts:
        top_dept = depts[0]
        lines.append(f"Heaviest hiring in {top_dept[0]} ({top_dept[1]} roles).")

    if signals:
        top_signal = signals[0]
        lines.append(f"Notable focus: {top_signal['category']} ({top_signal['count']} roles, {top_signal['percent']}%).")

    return " ".join(lines)


async def generate_executive_summary(result: dict, max_retries: int = 5) -> str:
    """
    Evaluator Agent: Generates a detailed 150-250 word executive summary
    synthesizing all competitive intelligence for a competitor.

    Args:
        result: Dict containing pricing_analysis, hiring_analysis, hiring_trends
        max_retries: Maximum retry attempts for API errors

    Returns:
        Detailed executive summary string
    """
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return "Unable to generate executive summary: API key not configured."

    client = genai.Client(api_key=api_key)
    model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    name = result.get('name', 'Unknown')
    pricing = result.get('pricing_analysis', {})
    hiring = result.get('hiring_analysis', {})
    trends = result.get('hiring_trends', {})

    # Build context for the evaluator
    context_parts = [f"Company: {name}"]

    # Pricing context
    if pricing and isinstance(pricing, dict):
        old_state = pricing.get('old_state', {})
        new_state = pricing.get('new_state', {})
        analysis = pricing.get('analysis', {})

        if old_state or new_state:
            context_parts.append("\n=== PRICING DATA ===")

            # Old pricing
            if old_state:
                old_plans = old_state.get('pricing_plans', [])
                if old_plans:
                    plans_str = ", ".join([f"{p.get('name', 'N/A')}: {p.get('price', 'N/A')}" for p in old_plans[:5]])
                    context_parts.append(f"6 months ago: {plans_str}")
                old_tagline = old_state.get('tagline', '')
                if old_tagline:
                    context_parts.append(f"Previous positioning: {old_tagline}")

            # New pricing
            if new_state:
                new_plans = new_state.get('pricing_plans', [])
                if new_plans:
                    plans_str = ", ".join([f"{p.get('name', 'N/A')}: {p.get('price', 'N/A')}" for p in new_plans[:5]])
                    context_parts.append(f"Current: {plans_str}")
                new_tagline = new_state.get('tagline', '')
                if new_tagline:
                    context_parts.append(f"Current positioning: {new_tagline}")

            # Analysis insights
            if analysis:
                change_detected = analysis.get('change_detected', False)
                context_parts.append(f"Pricing changed: {'Yes' if change_detected else 'No'}")

                evidence = analysis.get('evidence', {})
                if evidence:
                    for key, val in evidence.items():
                        if val and val != 'N/A':
                            context_parts.append(f"  {key}: {val}")

    # Hiring context
    if hiring and isinstance(hiring, dict):
        context_parts.append("\n=== HIRING DATA ===")
        total_jobs = hiring.get('total_jobs', 0)
        context_parts.append(f"Total open positions: {total_jobs}")

        top_depts = hiring.get('top_departments', [])
        if top_depts:
            depts_str = ", ".join([f"{d['name']} ({d['count']})" for d in top_depts[:5]])
            context_parts.append(f"Top departments: {depts_str}")

        signals = hiring.get('strategic_signals', [])
        if signals:
            signals_str = ", ".join([f"{s['category']} ({s['count']} roles, {s['percent']}%)" for s in signals[:4]])
            context_parts.append(f"Strategic signals: {signals_str}")

    # Trends context
    if trends and isinstance(trends, dict):
        context_parts.append("\n=== HIRING TRENDS ===")
        velocity = trends.get('velocity_change_percent', 0)
        old_count = trends.get('old_count', 0)
        new_count = trends.get('new_count', 0)
        context_parts.append(f"Hiring velocity change: {velocity:+.0f}% ({old_count} → {new_count} roles)")

        new_roles = trends.get('new_roles', [])
        if new_roles:
            roles_str = ", ".join([r.get('title', '')[:50] for r in new_roles[:5]])
            context_parts.append(f"New roles added: {roles_str}")

    # Background context
    background = result.get('background', {})
    if background and isinstance(background, dict):
        summary = background.get('summary', {})
        if summary:
            context_parts.append("\n=== COMPANY BACKGROUND ===")

            if summary.get('founded'):
                context_parts.append(f"Founded: {summary['founded']}")
            if summary.get('founders'):
                context_parts.append(f"Founders: {summary['founders']}")
            if summary.get('headquarters'):
                context_parts.append(f"Headquarters: {summary['headquarters']}")
            if summary.get('employees'):
                context_parts.append(f"Employees: {summary['employees']}")
            if summary.get('funding'):
                context_parts.append(f"Total funding: ${summary['funding']}")
            if summary.get('industry'):
                context_parts.append(f"Industry: {summary['industry']}")
            if summary.get('description'):
                # Truncate description
                desc = summary['description'][:300]
                context_parts.append(f"Description: {desc}...")

        # Recent news
        news = background.get('recent_news', [])
        if news:
            context_parts.append("\nRecent news headlines:")
            for item in news[:3]:
                context_parts.append(f"  - {item.get('title', '')[:80]}")

        # GitHub activity
        github = background.get('github', {})
        if github:
            repos = github.get('public_repos', 0)
            stars = github.get('total_stars', 0)
            if repos or stars:
                context_parts.append(f"\nOpen source: {repos} repos, {stars} stars")

    # Homepage analysis context
    homepage = result.get('homepage_analysis', {})
    if homepage and isinstance(homepage, dict) and 'error' not in homepage:
        context_parts.append("\n=== HOMEPAGE INTELLIGENCE ===")
        new_state = homepage.get('new_state', {})
        analysis = homepage.get('analysis', {})

        if new_state:
            hero = new_state.get('hero_headline', '')
            if hero:
                context_parts.append(f"Current positioning: {hero}")
            audience = new_state.get('target_audience', '')
            if audience:
                context_parts.append(f"Target audience: {audience}")
            value_props = new_state.get('value_propositions', [])
            if value_props:
                context_parts.append(f"Value props: {', '.join(value_props[:3])}")

        if analysis:
            change_detected = analysis.get('change_detected', False)
            if change_detected:
                shift = analysis.get('strategic_shift', '')
                if shift:
                    context_parts.append(f"Homepage strategic shift: {shift}")

    context = "\n".join(context_parts)

    # Evaluator prompt
    system_instruction = """You are a senior competitive intelligence analyst writing executive briefings for C-level executives.

Your task is to synthesize all available data into a compelling, insight-rich executive summary.

Guidelines:
- Write 150-250 words (this is critical - not too short, not too long)
- Lead with the most important strategic insight
- Be specific with data points (numbers, percentages, changes)
- Identify strategic implications and potential threats/opportunities
- Use confident, direct language appropriate for executive audiences
- Do NOT use bullet points - write in flowing paragraphs
- Do NOT include headers or sections - one cohesive summary
- Avoid vague statements - be specific and actionable
- If data is limited, acknowledge it but still provide value from what's available"""

    user_prompt = f"""Write an executive summary for the following competitive intelligence on {name}:

{context}

Remember: 150-250 words, flowing paragraphs, lead with the key insight, be specific with data."""

    config = types.GenerateContentConfig(
        system_instruction=system_instruction
    )

    retryable_errors = ['429', '503', 'RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'overloaded']

    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model_id,
                contents=user_prompt,
                config=config
            )
            summary = response.text.strip()

            # Basic validation
            word_count = len(summary.split())
            if word_count < 50:
                return f"Analysis shows limited strategic changes for {name}. Insufficient data available for detailed assessment."

            return summary

        except Exception as e:
            error_str = str(e)
            is_retryable = any(code in error_str for code in retryable_errors)

            if is_retryable and attempt < max_retries - 1:
                wait_time = (2 ** attempt) * 2
                print(f"  ⚠️  Evaluator API overloaded (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                print(f"  ✗ Evaluator failed: {error_str}")
                return f"Unable to generate executive summary due to API error."

    return "Executive summary generation failed after maximum retries."


async def analyze_competitor(competitor: dict, months_ago: int = 6) -> dict:
    """
    Run full analysis on a single competitor.
    Returns combined pricing + hiring intelligence.
    """
    name = competitor['name']
    pricing_url = competitor.get('pricing_url')
    ats_url = competitor.get('ats_url')
    ats_type = competitor.get('ats_type')

    print(f"\n{'='*60}")
    print(f"  ANALYZING: {name}")
    print(f"{'='*60}")

    result = {
        'name': name,
        'domain': competitor.get('domain'),
        'pricing_url': pricing_url,
        'ats_url': ats_url,
        'pricing_analysis': None,
        'hiring_analysis': None,
        'hiring_trends': None,
        'homepage_analysis': None,
        'timestamp': datetime.now().isoformat()
    }

    # --- 1. Pricing/Positioning Analysis (Sentinel Probe) ---
    # Try pricing analysis even if not verified - the URL might still work
    if pricing_url:
        print(f"\n📊 Running Sentinel Probe on {pricing_url}...")
        try:
            # Get current state
            current_md = await get_current_state(pricing_url)

            if not current_md or len(current_md.strip()) < 100:
                print(f"  ⚠ Could not fetch pricing page content")
            else:
                # Get historical state
                old_md, snapshot_url = get_historical_state(pricing_url, months_ago)

                if old_md and current_md:
                    print(f"  Found historical snapshot from ~{months_ago} months ago")
                    # Run full diff analysis
                    analysis = await analyze_diff(
                        old_md=old_md,
                        new_md=current_md,
                        target_url=pricing_url
                    )
                    result['pricing_analysis'] = analysis
                    result['historical_snapshot'] = snapshot_url
                    print(f"  ✓ Pricing analysis complete (with historical comparison)")
                elif current_md:
                    # No historical data - still analyze current pricing
                    print(f"  ⚠ No historical snapshot, analyzing current pricing only...")
                    analysis = await analyze_diff(
                        old_md=None,
                        new_md=current_md,
                        target_url=pricing_url
                    )
                    result['pricing_analysis'] = analysis
                    print(f"  ✓ Current pricing analysis complete (no historical data)")
        except Exception as e:
            print(f"  ✗ Pricing analysis failed: {e}")
    else:
        print(f"\n📊 Skipping pricing analysis (no pricing URL)")

    # --- 2. Job Listings Analysis (Ghost Probe) ---
    # Strategy: Try multiple sources and aggregate for comprehensive coverage
    jobs = []
    job_sources = []

    def _dedupe_jobs(job_list: list[dict]) -> list[dict]:
        """Deduplicate jobs by title (case-insensitive)."""
        seen = set()
        unique = []
        for job in job_list:
            # Normalize title for comparison
            title_key = job.get('title', '').lower().strip()
            if title_key and title_key not in seen:
                seen.add(title_key)
                unique.append(job)
        return unique

    # Source 1: ATS (Greenhouse/Lever/Ashby APIs - returns ALL jobs)
    if ats_url and ats_type:
        print(f"\n👻 Running Ghost Probe on {ats_url}...")
        try:
            ats_jobs = fetch_jobs(ats_url, ats_type)
            if ats_jobs:
                jobs.extend(ats_jobs)
                job_sources.append(f"{ats_type}:{ats_url}")
                print(f"  ✓ ATS returned {len(ats_jobs)} positions")
            else:
                print(f"  ⚠ No jobs found from ATS")
        except Exception as e:
            print(f"  ✗ ATS fetch failed: {e}")

    # Source 2: levels.fyi (supplementary - limited to ~15 jobs but may have different listings)
    levelsfyi_slug = competitor.get('levelsfyi_slug') or name
    if not jobs or len(jobs) < 20:  # Try if no jobs or few jobs from ATS
        print(f"\n👻 Checking levels.fyi for additional jobs...")
        try:
            levelsfyi_jobs = fetch_jobs_from_levelsfyi(levelsfyi_slug)
            if levelsfyi_jobs:
                jobs.extend(levelsfyi_jobs)
                job_sources.append(f"levels.fyi/{levelsfyi_slug}")
                result['levelsfyi_url'] = f"https://www.levels.fyi/jobs/company/{levelsfyi_slug.lower().replace(' ', '').replace('.', '')}"
        except Exception as e:
            print(f"  ✗ levels.fyi failed: {e}")

    # Source 3: LinkedIn (supplementary - may have jobs not listed elsewhere)
    if not jobs or len(jobs) < 30:  # Try if still need more coverage
        print(f"\n👻 Checking LinkedIn for additional jobs...")
        try:
            linkedin_jobs = fetch_jobs_from_linkedin(name, max_results=100)
            if linkedin_jobs:
                jobs.extend(linkedin_jobs)
                job_sources.append(f"linkedin:{name}")
        except Exception as e:
            print(f"  ✗ LinkedIn failed: {e}")

    # Source 4: Direct careers page with AI extraction (last resort)
    if not jobs and competitor.get('careers_url'):
        print(f"\n👻 Trying AI extraction from careers page...")
        try:
            direct_jobs = fetch_jobs_direct_careers(competitor['careers_url'], name)
            if direct_jobs:
                jobs.extend(direct_jobs)
                job_sources.append(f"direct:{competitor['careers_url']}")
        except Exception as e:
            print(f"  ✗ Direct extraction failed: {e}")

    # Deduplicate jobs from all sources
    if jobs:
        original_count = len(jobs)
        jobs = _dedupe_jobs(jobs)
        if original_count != len(jobs):
            print(f"  📋 Deduplicated: {original_count} → {len(jobs)} unique jobs")

    job_source = " + ".join(job_sources) if job_sources else None

    # Process jobs if we have any
    if jobs:
        print(f"  ✓ Total: {len(jobs)} jobs from {job_source}")
        result['job_source'] = job_source

        # Analyze current jobs
        result['hiring_analysis'] = analyze_jobs_with_ai(jobs, name)

        # Load previous snapshot for trend comparison
        previous_jobs = load_previous_snapshot(name)
        if previous_jobs:
            print(f"  Comparing with previous snapshot ({len(previous_jobs)} jobs)")
            result['hiring_trends'] = analyze_hiring_trends(previous_jobs, jobs)
        else:
            print(f"  No previous snapshot (first run)")

        # Save current as new snapshot
        save_snapshot(name, jobs, job_source or 'unknown')
    else:
        print(f"\n👻 No job data available for {name} (tried ATS, levels.fyi, direct)")

    # --- 3. Background Intelligence (Background Probe) ---
    print(f"\n🔍 Running Background Probe...")
    try:
        domain = competitor.get('domain', '').replace('https://', '').replace('http://', '')
        background = gather_company_background(
            company_name=name,
            domain=domain,
            include_news=True,
            include_github=True
        )

        # Store full background data for the report
        result['background'] = {
            'summary': background.get('summary', {}),
            'sources_used': list(background.get('sources', {}).keys()),
            'wikipedia': background.get('sources', {}).get('wikipedia'),
            'recent_news': background.get('sources', {}).get('news', [])[:5],
            'github': background.get('sources', {}).get('github'),
        }

        # Extract key facts for display
        summary = background.get('summary', {})
        facts = []
        if summary.get('founded'):
            facts.append(f"Founded: {summary['founded']}")
        if summary.get('employees'):
            facts.append(f"Employees: {summary['employees']}")
        if summary.get('funding'):
            facts.append(f"Funding: ${summary['funding']}")
        if summary.get('headquarters'):
            facts.append(f"HQ: {summary['headquarters']}")

        if facts:
            print(f"  ✓ Background: {', '.join(facts)}")
        else:
            print(f"  ✓ Background gathered from {len(result['background']['sources_used'])} sources")
    except Exception as e:
        print(f"  ✗ Background probe failed: {e}")
        result['background'] = None

    # --- 4. Homepage Analysis (Spy Report) ---
    domain = competitor.get('domain', '')
    if domain:
        homepage_url = f"https://{domain.replace('https://', '').replace('http://', '')}"
        print(f"\n🕵️ Running Spy Report on {homepage_url}...")
        try:
            homepage_result = await analyze_homepage(homepage_url, months_ago)
            if homepage_result and 'error' not in homepage_result:
                result['homepage_analysis'] = homepage_result
                change_detected = homepage_result.get('analysis', {}).get('change_detected', False)
                if change_detected:
                    shift = homepage_result.get('analysis', {}).get('strategic_shift', 'Changes detected')
                    print(f"  ✓ Homepage analysis complete: {shift[:60]}...")
                else:
                    print(f"  ✓ Homepage analysis complete (no major changes)")
            else:
                print(f"  ⚠ Homepage analysis failed: {homepage_result.get('error', 'Unknown error')}")
        except Exception as e:
            print(f"  ✗ Homepage analysis failed: {e}")
    else:
        print(f"\n🕵️ Skipping homepage analysis (no domain)")

    # --- 5. Executive Summary (Evaluator Agent) ---
    print(f"\n🎯 Running Evaluator Agent...")
    try:
        executive_summary = await generate_executive_summary(result)
        result['executive_summary'] = executive_summary
        print(f"  ✓ Executive summary generated ({len(executive_summary.split())} words)")
    except Exception as e:
        print(f"  ✗ Evaluator failed: {e}")
        result['executive_summary'] = "Executive summary unavailable."

    return result


async def run_pipeline(description: str = None, competitor_names: list[str] = None, months: int = 6) -> list[dict]:
    """
    Main orchestration pipeline.

    Args:
        description: Product description to find competitors for
        competitor_names: Or, provide explicit competitor names
        months: How far back to look for historical pricing data

    Returns:
        List of analysis results for each competitor
    """
    ensure_dirs()

    print("\n" + "="*60)
    print("  SENTINEL COMPETITIVE INTELLIGENCE PIPELINE")
    print("="*60)

    # --- Step 1: Discovery ---
    if competitor_names:
        # Manual competitor list provided - need to look up domains
        print(f"\n🎯 Using provided competitors: {competitor_names}")
        print("🧠 Looking up domains...")

        # Use Gemini to get domains for the provided names

        api_key = os.getenv("GEMINI_API_KEY")
        if api_key:
            client = genai.Client(api_key=api_key)
            model_id = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

            prompt = f"""For each company name, provide their main website domain.
Return a JSON array of objects with "name" and "domain" fields.
Companies: {', '.join(competitor_names)}
Example: [{{"name": "Asana", "domain": "asana.com"}}]"""

            config = types.GenerateContentConfig(
                response_mime_type="application/json"
            )

            # Retry with exponential backoff
            retryable_errors = ['429', '503', 'RESOURCE_EXHAUSTED', 'UNAVAILABLE', 'overloaded']
            max_retries = 5
            comp_data = None

            for attempt in range(max_retries):
                try:
                    response = client.models.generate_content(
                        model=model_id, contents=prompt, config=config
                    )
                    comp_data = json.loads(response.text.strip())
                    break
                except Exception as e:
                    error_str = str(e)
                    is_retryable = any(code in error_str for code in retryable_errors)

                    if is_retryable and attempt < max_retries - 1:
                        wait_time = (2 ** attempt) * 2
                        print(f"  ⚠️  API overloaded (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                    else:
                        print(f"Failed to look up domains: {e}")
                        comp_data = [{'name': n, 'domain': None} for n in competitor_names]
                        break

            if comp_data is None:
                comp_data = [{'name': n, 'domain': None} for n in competitor_names]
        else:
            comp_data = [{'name': n, 'domain': None} for n in competitor_names]

        # Now run discovery for each
        competitors = []
        for comp in comp_data:
            if not comp.get('domain'):
                print(f"  ⚠ No domain for {comp.get('name')}, skipping")
                continue
            links = find_company_links(comp)
            if links:
                # Try to find ATS
                if links.get('careers_url'):
                    ats = detect_ats(links['careers_url'])
                    if not ats:
                        ats = try_common_ats_urls(links['name'])
                    if ats:
                        links['ats_url'] = ats['url']
                        links['ats_type'] = ats['type']
                competitors.append(links)
    else:
        # Auto-discover competitors
        print(f"\n🧠 Discovering competitors for: {description[:50]}...")
        from discovery import run_discovery
        competitors = run_discovery(description)

    if not competitors:
        print("❌ No competitors found. Exiting.")
        return []

    print(f"\n📋 Analyzing {len(competitors)} competitors...")

    # --- Step 2: Analyze Each Competitor ---
    results = []
    for comp in competitors:
        try:
            analysis = await analyze_competitor(comp, months)
            results.append(analysis)
        except Exception as e:
            print(f"❌ Failed to analyze {comp.get('name')}: {e}")
            results.append({
                'name': comp.get('name'),
                'error': str(e)
            })

    # --- Step 3: Save Combined Results ---
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(REPORTS_DIR, f"intelligence_{timestamp}.json")
    with open(output_file, 'w') as f:
        json.dump({
            'generated_at': datetime.now().isoformat(),
            'description': description,
            'competitor_count': len(results),
            'results': results
        }, f, indent=2)

    print(f"\n{'='*60}")
    print(f"  PIPELINE COMPLETE")
    print(f"  Results saved to: {output_file}")
    print(f"{'='*60}")

    return results


def print_summary(results: list[dict]):
    """Print a summary of the analysis results."""
    print("\n" + "="*60)
    print("  EXECUTIVE SUMMARY")
    print("="*60)

    for r in results:
        name = r.get('name', 'Unknown')
        print(f"\n[{name}]")

        # Executive summary from evaluator
        exec_summary = r.get('executive_summary', '')
        if exec_summary and exec_summary != "Executive summary unavailable.":
            # Show first 300 chars of executive summary
            if len(exec_summary) > 300:
                exec_summary = exec_summary[:300] + "..."
            print(f"  📋 {exec_summary}")
        else:
            # Fallback to individual summaries
            # Pricing summary
            pricing = r.get('pricing_analysis', {})
            if pricing and isinstance(pricing, dict):
                analysis = pricing.get('analysis', {})
                if isinstance(analysis, dict):
                    strategic = (
                        analysis.get('strategic_shift') or
                        analysis.get('strategic_analysis') or
                        analysis.get('summary')
                    )
                    if strategic and strategic != 'N/A':
                        if len(strategic) > 150:
                            strategic = strategic[:150] + "..."
                        print(f"  💰 Pricing: {strategic}")

            # Hiring summary
            hiring = r.get('hiring_analysis', {})
            if hiring and isinstance(hiring, dict):
                summary = hiring.get('summary')
                if summary:
                    print(f"  👥 Hiring: {summary}")

            # Trends
            trends = r.get('hiring_trends', {})
            if trends and isinstance(trends, dict):
                trend_summary = trends.get('summary')
                if trend_summary:
                    print(f"  📈 Trend: {trend_summary}")

            # Background
            background = r.get('background', {})
            if background and isinstance(background, dict):
                summary = background.get('summary', {})
                bg_parts = []
                if summary.get('founded'):
                    bg_parts.append(f"Founded: {summary['founded']}")
                if summary.get('employees'):
                    bg_parts.append(f"Employees: {summary['employees']}")
                if summary.get('headquarters'):
                    bg_parts.append(f"HQ: {summary['headquarters']}")
                if bg_parts:
                    print(f"  🏢 Background: {', '.join(bg_parts)}")


def main():
    parser = argparse.ArgumentParser(
        description="Sentinel Competitive Intelligence Pipeline"
    )
    parser.add_argument(
        "description",
        nargs="?",
        help="Description of your product/company to find competitors"
    )
    parser.add_argument(
        "--competitors", "-c",
        help="Comma-separated list of competitor names (skip auto-discovery)"
    )
    parser.add_argument(
        "--months", "-m",
        type=int,
        default=6,
        help="Months of historical data to analyze (default: 6)"
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file path"
    )

    args = parser.parse_args()

    if not args.description and not args.competitors:
        parser.error("Either provide a description or --competitors list")

    competitor_names = None
    if args.competitors:
        competitor_names = [c.strip() for c in args.competitors.split(",")]

    # Run the pipeline
    results = asyncio.run(run_pipeline(
        description=args.description,
        competitor_names=competitor_names,
        months=args.months
    ))

    # Print summary
    print_summary(results)


if __name__ == "__main__":
    main()
