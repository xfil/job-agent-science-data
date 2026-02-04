"""
job-agent-science-data: V1 Job Monitoring Agent

What this script does:
- Reads the preferences (profile.yaml) and job sources (sources.yaml)
- Downloads job postings from each source (RSS or HTML page)
- Scores each posting using a simple keyword-based approach
- Prints any "good matches" above the threshold
- Remembers what it has already seen in seen.json (to avoid duplicates)

Design philosophy for V1:
- Keep it simple and working end-to-end
- Prefer RSS feeds when available (stable + ToS-friendly)
- For HTML pages, do a *very simple* extraction first - to be improved
"""

# -----------------------------
# Imports (libraries we use)
# -----------------------------
import os          # For checking if files exist (seen.json)
import json        # For reading/writing seen.json
import requests    # For downloading RSS and HTML content
import yaml        # For reading profile.yaml and sources.yaml
from bs4 import BeautifulSoup  # For parsing RSS (XML) and HTML pages


# -----------------------------
# Constants (single place to edit)
# -----------------------------

# This file stores the job links we've already processed.
# Why? Because GitHub Actions runs fresh each time, and we need persistence.
SEEN_FILE = "seen.json"


# -----------------------------
# Helper: read a YAML file safely
# -----------------------------
def load_yaml(path: str) -> dict:
    """
    Load YAML configuration from disk.

    Args:
        path: Path to YAML file.

    Returns:
        Parsed YAML as a Python dict.
    """
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# -----------------------------
# Helper: load "seen" set from JSON
# -----------------------------
def load_seen() -> set:
    """
    Load previously seen job URLs from seen.json.

    Returns:
        A Python set of URLs (fast membership checks).
    """
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            # seen.json stores a list; we convert to set for speed.
            return set(json.load(f))

    # If the file doesn't exist yet, nothing has been seen.
    return set()


# -----------------------------
# Helper: save "seen" set to JSON
# -----------------------------
def save_seen(seen: set) -> None:
    """
    Save the updated set of seen URLs back to seen.json.

    Args:
        seen: Set of job URLs already processed.
    """
    # JSON can't store sets, so we convert back to a list.
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, indent=2)


# -----------------------------
# Source fetcher: RSS feeds
# -----------------------------
def fetch_rss(url: str) -> list[dict]:
    """
    Download an RSS feed and return a list of "job-like" items.

    The RSS format typically contains <item> blocks, each with:
      - <title>
      - <link>
      - <description>

    Args:
        url: RSS URL.

    Returns:
        List of dicts: {title, link, description}
    """
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()  # Raises an error if status != 200 (helps debugging)

    # Parse as XML (RSS is XML)
    soup = BeautifulSoup(resp.text, "xml")

    items = []
    for item in soup.find_all("item"):
        title = item.title.text.strip() if item.title else ""
        link = item.link.text.strip() if item.link else ""
        desc = item.description.text.strip() if item.description else ""

        # Keep a consistent schema for downstream processing
        items.append({
            "title": title,
            "link": link,
            "description": desc,
        })

    return items


# -----------------------------
# Source fetcher: HTML page links (simple V1)
# -----------------------------
def fetch_html_links(url: str) -> list[dict]:
    """
    Download an HTML page and extract links.

    IMPORTANT:
    - This V1 method is intentionally simple.
    - It collects many links that are NOT jobs.
    - Later we will write a source-specific parser (much higher precision).

    Args:
        url: Page URL.

    Returns:
        List of dicts: {title, link, description}
    """
    resp = requests.get(url, timeout=20)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")

    items = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        link = a["href"].strip()

        # We only accept absolute URLs here to avoid broken relative links.
        # Later we can resolve relative URLs properly if needed.
        if title and link.startswith("http"):
            items.append({
                "title": title,
                "link": link,
                "description": "",
            })

    return items


# -----------------------------
# Scoring function (simple keyword scoring)
# -----------------------------
def score_job(job: dict, profile: dict) -> float:
    """
    Score a job based on keyword hits.

    How scoring works in V1:
    - For each keyword in skills_keywords:
        +0.05 if the keyword appears in the job text
    - For each keyword in exclude_keywords:
        -0.30 if the keyword appears in the job text
    - Final score clipped to [0, 1]

    This is a simple baseline. Later we can:
    - add category-specific weights (museum vs academic vs industry)
    - use better NLP similarity
    - add country detection for the museum Portugal exclusion

    Args:
        job: Dict with title/description fields.
        profile: Dict from profile.yaml.

    Returns:
        Score between 0.0 and 1.0.
    """
    # Combine title and description into one searchable text
    text = f"{job.get('title','')} {job.get('description','')}".lower()

    score = 0.0

    # Add small points for each desired keyword found
    for kw in profile.get("skills_keywords", []):
        if kw.lower() in text:
            score += 0.05

    # Apply strong penalties for exclusion keywords
    for kw in profile.get("exclude_keywords", []):
        if kw.lower() in text:
            score -= 0.30

    # Clip score to [0, 1]
    if score < 0:
        score = 0.0
    if score > 1:
        score = 1.0

    return score


# -----------------------------
# Main pipeline
# -----------------------------
def main() -> None:
    """
    Orchestrates the full workflow:
    1) Load config
    2) Load seen state
    3) Fetch jobs from each source
    4) Filter out already-seen links
    5) Score new jobs
    6) Save seen
    7) Print matches
    """
    # Load preferences (your keywords, threshold, etc.)
    profile = load_yaml("profile.yaml")

    # Load sources (list of RSS/HTML sources)
    sources_cfg = load_yaml("sources.yaml")
    sources = sources_cfg.get("sources", [])

    # Load previously seen job links so we don't alert twice
    seen = load_seen()

    # We'll store matches found in this run
    new_hits = []

    # Loop through sources one by one
    for src in sources:
        src_name = src.get("name", "Unknown source")
        src_type = src.get("type")
        src_url = src.get("url")

        if not src_type or not src_url:
            # Skip misconfigured sources instead of crashing
            print(f"[WARN] Source '{src_name}' is missing type or url; skipping.")
            continue

        print(f"[INFO] Fetching from {src_name} ({src_type})...")

        # Fetch data depending on source type
        try:
            if src_type == "rss":
                jobs = fetch_rss(src_url)
            elif src_type == "html":
                jobs = fetch_html_links(src_url)
            else:
                print(f"[WARN] Unknown source type '{src_type}' for '{src_name}'; skipping.")
                continue
        except Exception as e:
            # If one source fails, we continue with the rest
            print(f"[ERROR] Failed to fetch {src_name}: {e}")
            continue

        # Process each fetched "job item"
        for job in jobs:
            link = job.get("link", "").strip()

            # If there's no link, we can't dedupe reliably; skip
            if not link:
                continue

            # Skip links we already processed in previous runs
            if link in seen:
                continue

            # Score the posting
            score = score_job(job, profile)

            # If it meets threshold, keep it as a hit
            threshold = float(profile.get("score_threshold", 0.75))
            if score >= threshold:
                new_hits.append((score, job, src_name))

            # Mark as seen regardless of score so we don't re-process it forever.
            # NOTE: Some people prefer marking only the alerted ones as seen.
            # We'll revisit this decision if you want.
            seen.add(link)

    # Save updated seen list to disk (and later GitHub Actions commits it)
    save_seen(seen)

    # Print results for GitHub Actions logs
    if new_hits:
        print("\n=== New matching jobs ===")
        for score, job, src_name in sorted(new_hits, key=lambda x: x[0], reverse=True):
            print(f"- [{score:.2f}] ({src_name}) {job.get('title','(no title)')} → {job.get('link')}")
    else:
        print("\nNo new matching jobs today.")


# -----------------------------
# Python entry point
# -----------------------------
if __name__ == "__main__":
    # This ensures main() runs only when you execute: python agent.py
    # (and not if you import agent.py from another file)
    main()

