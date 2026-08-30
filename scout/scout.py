"""The scout — Nano's cloud browser worker (agentic phase 1a).

Polls the task queue, researches the open web (search + read, no logins,
no purchases), and reports a structured shortlist. Every step is bounded:
max 12 actions, text-mode reading, images blocked. Stub mode without an
API key returns a canned shortlist so the pipeline is testable end to end.
"""
import json
import os
import re
import time

import httpx

API = os.environ.get("SCOUT_API_URL", "http://api:8000")
WORKER_TOKEN = os.environ["SUPERAPP_WORKER_TOKEN"]
ANTHROPIC_KEY = os.environ.get("SUPERAPP_ANTHROPIC_API_KEY", "")
MODEL = os.environ.get("SCOUT_MODEL", "claude-opus-5")
H = {"Authorization": f"Bearer {WORKER_TOKEN}"}

SYSTEM = (
    "You are Nano's scout: a careful web researcher running a real browser. "
    "You get a person's errand and must produce a concrete shortlist. Work in "
    "steps; at each step choose ONE action:\n"
    "- search: a web search (state the query)\n"
    "- open: open one URL from what you've seen (state the url)\n"
    "- done: finish with your shortlist\n"
    "Prefer marketplace/listing pages over blogspam. Extract REAL items with "
    "prices and locations when present; never invent listings — if the web "
    "gave you nothing solid, say so in caveats. Keep queries specific. "
    "You have at most 12 steps; budget them."
)
STEP_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "action": {"type": "string", "enum": ["search", "open", "done"]},
        "query": {"type": "string"},
        "url": {"type": "string"},
        "summary": {"type": "string"},
        "shortlist": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "price": {"type": "string"},
                "location": {"type": "string"},
                "url": {"type": "string"},
                "why": {"type": "string"},
            },
            "required": ["title", "price", "location", "url", "why"],
            "additionalProperties": False,
        }},
        "caveats": {"type": "string"},
    },
    "required": ["thought", "action", "query", "url", "summary", "shortlist", "caveats"],
    "additionalProperties": False,
}


def search_web(query: str) -> list[dict]:
    """DuckDuckGo's HTML endpoint — no JS, no key, generous."""
    try:
        r = httpx.get("https://html.duckduckgo.com/html/", params={"q": query},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
        results = []
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', r.text):
            url, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2))
            if url.startswith("//duckduckgo.com/l/?uddg="):
                url = httpx.URL("https:" + url).params.get("uddg", url)
            results.append({"title": title.strip()[:120], "url": str(url)[:300]})
            if len(results) >= 8:
                break
        return results
    except httpx.HTTPError:
        return []


def read_page(browser, url: str) -> str:
    page = browser.new_page()
    try:
        page.route("**/*", lambda route: route.abort()
                   if route.request.resource_type in ("image", "media", "font")
                   else route.continue_())
        page.goto(url, timeout=25000, wait_until="domcontentloaded")
        page.wait_for_timeout(1200)
        text = page.evaluate("document.body ? document.body.innerText : ''")
        return re.sub(r"\n{3,}", "\n\n", text)[:7000]
    except Exception as exc:  # noqa: BLE001 — any nav failure is a finding
        return f"[could not load {url}: {type(exc).__name__}]"
    finally:
        page.close()


def run_task(browser, instruction: str) -> dict:
    if not ANTHROPIC_KEY:
        return {"summary": f"(stub) Scouted for: {instruction[:80]}",
                "shortlist": [{"title": "Stub find", "price": "$0",
                               "location": "nowhere", "url": "https://example.com",
                               "why": "worker has no API key"}],
                "caveats": "stub mode"}
    from anthropic import Anthropic

    client = Anthropic(api_key=ANTHROPIC_KEY)
    transcript: list[dict] = [{"role": "user", "content": json.dumps(
        {"errand": instruction, "step": 0})}]
    for step in range(12):
        resp = client.messages.create(
            model=MODEL, max_tokens=2000,
            system=[{"type": "text", "text": SYSTEM,
                     "cache_control": {"type": "ephemeral"}}],
            messages=transcript,
            output_config={"format": {"type": "json_schema", "schema": STEP_SCHEMA}},
        )
        text = "".join(b.text for b in resp.content if b.type == "text")
        transcript.append({"role": "assistant", "content": text})
        try:
            move = json.loads(text)
        except json.JSONDecodeError:
            break
        if move["action"] == "done":
            return {"summary": move["summary"][:400],
                    "shortlist": move["shortlist"][:6],
                    "caveats": move["caveats"][:300]}
        if move["action"] == "search":
            observation = {"search_results": search_web(move["query"])}
        else:
            observation = {"page_text": read_page(browser, move["url"])}
        transcript.append({"role": "user", "content": json.dumps(
            {"step": step + 1, "observation": observation}, ensure_ascii=False)[:9000]})
    return {"summary": "Ran out of steps before a confident shortlist.",
            "shortlist": [], "caveats": "hit the step budget; try a narrower errand"}


def main() -> None:
    from playwright.sync_api import sync_playwright

    print("scout up; polling", API)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        while True:
            try:
                r = httpx.get(f"{API}/v1/tasks/next", headers=H, timeout=15)
                task = r.json().get("task") if r.status_code == 200 else None
            except httpx.HTTPError:
                task = None
            if not task:
                time.sleep(15)
                continue
            print("task", task["id"], task["instruction"][:80])
            try:
                result = run_task(browser, task["instruction"])
                httpx.post(f"{API}/v1/tasks/{task['id']}/complete", headers=H,
                           json={"result": result}, timeout=30)
                print("done", task["id"])
            except Exception as exc:  # noqa: BLE001 — report, never crash the loop
                httpx.post(f"{API}/v1/tasks/{task['id']}/fail", headers=H,
                           json={"error": str(exc)[:500]}, timeout=30)
                print("failed", task["id"], exc)


if __name__ == "__main__":
    main()
