# Sentinel - Agentic Competitive Intelligence

An automated competitive intelligence pipeline that monitors competitors' pricing changes, hiring patterns, and strategic shifts using AI-powered analysis.

## Overview

Sentinel combines multiple data sources and AI analysis to provide actionable competitive intelligence:

- **Pricing Analysis** - Tracks pricing page changes over time using Wayback Machine snapshots
- **Hiring Intelligence** - Scrapes job listings from ATS platforms (Greenhouse, Lever, Ashby) or levels.fyi
- **Strategic Signals** - Identifies focus areas (AI/ML, Enterprise, Platform, etc.) from hiring patterns
- **Executive Summaries** - AI-generated briefings synthesizing all intelligence

## Architecture

```
┌─────────────────┐     ┌──────────────────┐     ┌─────────────────┐
│   Discovery     │────▶│  Sentinel Probe  │────▶│   Orchestrator  │
│  (Competitors)  │     │ (Pricing Diffs)  │     │  (Pipeline)     │
└─────────────────┘     └──────────────────┘     └─────────────────┘
                                                          │
┌─────────────────┐     ┌──────────────────┐              ▼
│   Ghost Probe   │────▶│  Evaluator Agent │     ┌─────────────────┐
│  (Job Scraping) │     │ (AI Summaries)   │────▶│  JSON Reports   │
└─────────────────┘     └──────────────────┘     └─────────────────┘
```

### Components

| Component | File | Purpose |
|-----------|------|---------|
| **Discovery** | `discovery.py` | Finds competitors via AI, locates pricing/careers URLs |
| **Sentinel Probe** | `sentinel_probe.py` | Compares current vs historical pricing pages |
| **Ghost Probe** | `ghost_probe.py` | Scrapes job listings from ATS or levels.fyi |
| **Orchestrator** | `orchestrator.py` | Coordinates the full pipeline |
| **Report Generator** | `generate_report.py` | Creates PDF reports from analysis |

## Setup

### 1. Install Dependencies

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install requirements
pip install -r requirements.txt
```

### 2. Configure API Key

Create a `.env` file with your Gemini API key:

```bash
GEMINI_API_KEY=your_api_key_here
GEMINI_MODEL=gemini-1.5-flash  # Optional: defaults to gemini-1.5-flash
```

Get a Gemini API key at: https://makersuite.google.com/app/apikey

### 3. Install LaTeX (for PDF reports)

```bash
# macOS
brew install --cask basictex

# Ubuntu/Debian
sudo apt-get install texlive-latex-base
```

## Usage

### Full Pipeline (Recommended)

Analyze competitors by description:

```bash
python orchestrator.py "Project management software for engineering teams"
```

Or specify competitors directly:

```bash
python orchestrator.py --competitors "Linear,Asana,Monday.com,ClickUp,Jira"
```

Options:
- `--months N` - Look back N months for historical data (default: 6)
- `--output FILE` - Custom output file path

### Individual Components

**Ghost Probe** - Scan job listings:

```bash
# Auto-detect ATS from careers page
python ghost_probe.py https://linear.app/careers

# Save results for later comparison
python ghost_probe.py https://linear.app/careers -o linear_jobs.json

# Compare with previous snapshot
python ghost_probe.py https://linear.app/careers --compare linear_jobs_old.json

# Direct ATS URL (if detection fails)
python ghost_probe.py https://linear.app/careers --ats-url https://jobs.ashbyhq.com/linear --ats-type ashby
```

**Sentinel Probe** - Analyze pricing changes:

```bash
python main.py https://vercel.com/pricing --months 6
```

**Generate PDF Report**:

```bash
python generate_report.py --url https://vercel.com/pricing
```

## Output

Results are saved to the `reports/` directory as JSON:

```json
{
  "generated_at": "2024-02-05T18:01:22",
  "competitor_count": 5,
  "results": [
    {
      "name": "Linear",
      "pricing_analysis": { ... },
      "hiring_analysis": {
        "total_jobs": 45,
        "top_departments": [...],
        "strategic_signals": [...]
      },
      "executive_summary": "Linear is aggressively expanding..."
    }
  ]
}
```

Job snapshots are stored in `snapshots/` for trend analysis over time.

## Supported ATS Platforms

| Platform | Detection | API |
|----------|-----------|-----|
| Greenhouse | Auto | HTML scraping |
| Lever | Auto | HTML scraping |
| Ashby | Auto | GraphQL API |
| levels.fyi | Fallback | HTML/JSON parsing |

For companies without standard ATS (Atlassian, Monday.com, etc.), the system falls back to levels.fyi which provides ~15 most relevant job listings per company.

## Limitations

- **Wayback Machine** - Historical snapshots may not exist for all pages
- **levels.fyi** - Limited to ~15 jobs per company (their rate limit)
- **JavaScript-heavy pages** - May not fully render for direct scraping
- **API Quotas** - Gemini free tier has rate limits; consider paid tier for heavy usage

## License

MIT
