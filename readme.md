# Agentic Competitor Analysis

## Setup

``` shell
brew install --cask basictex
```

## run

``` python
python3.11 generate_report.py --url https://vercel.com/pricing
```

``` python
python3.11 main.py https://vercel.com/pricing --months 6
```

``` shell
Basic usage - scan a careers page and list open roles:                                                                                                                                     
python ghost_probe.py https://linear.app/careers                                                                                                                                           

Save results to JSON for later comparison:                                                                                                                                                 
python ghost_probe.py https://linear.app/careers -o linear_jobs.json                                                                                                                     

Compare with a previous snapshot to analyze hiring trends:
python ghost_probe.py https://linear.app/careers --compare linear_jobs_old.json

Direct ATS URL if detection fails:
python ghost_probe.py https://linear.app/careers --ats-url https://jobs.lever.co/linear --ats-type lever
```
