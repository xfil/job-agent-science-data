import requests
import yaml
import json
import os
from bs4 import BeautifulSoup

SEEN_FILE = "seen.json"

def load_yaml(path):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f, indent=2)

def fetch_rss(url):
    resp = requests.get(url, timeout=20)
    soup = BeautifulSoup(resp.text, "xml")
    items = []
    for item in soup.find_all("item"):
        items.append({
            "title": item.title.text if item.title else "",
            "link": item.link.text if item.link else "",
            "description": item.description.text if item.description else "",
        })
    return items

def fetch_html_links(url):
    resp = requests.get(url, timeout=20)
    soup = BeautifulSoup(resp.text, "html.parser")
    items = []
    for a in soup.find_all("a", href=True):
        title = a.get_text(strip=True)
        link = a["href"]
        if title and link.startswith("http"):
            items.append({
                "title": title,
                "link": link,
                "description": "",
            })
    return items

def score_job(job, profile):
    text = f"{job['title']} {job['description']}".lower()
    score = 0.0

    for kw in profile["skills_keywords"]:
        if kw.lower() in text:
            score += 0.05

    for kw in profile["exclude_keywords"]:
        if kw.lower() in text:
            score -= 0.3

    return min(max(score, 0), 1)

def main():
    profile = load_yaml("profile.yaml")
    sources = load_yaml("sources.yaml")["sources"]
    seen = load_seen()

    new_hits = []

    for src in sources:
        if src["type"] == "rss":
            jobs = fetch_rss(src["url"])
        else:
            jobs = fetch_html_links(src["url"])

        for job in jobs:
            if job["link"] in seen:
                continue

            score = score_job(job, profile)

            if score >= profile["score_threshold"]:
                new_hits.append((score, job))
                seen.add(job["link"])

    save_seen(seen)

    if new_hits:
        print("New matching jobs:")
        for score, job in sorted(new_hits, reverse=True):
            print(f"- [{score:.2f}] {job['title']} → {job['link']}")
    else:
        print("No new matching jobs today.")

if __name__ == "__main__":
    main()
