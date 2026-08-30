"""The scout — Nano's cloud browser worker.

Phase 1a: bounded open-web research (search/open/done loop, no logins).
Phase 1b: a PERSISTENT browser profile (cookies survive restarts) plus a
one-time login handoff — the person opens a secret link on their phone,
sees the scout's live browser as streamed frames, taps and types to log
in (Facebook etc.), and the session then belongs to the profile. After
that, marketplace errands run against the logged-in session.

Boundaries: research never logs in; the remote window is armed only on an
explicit connect errand and disarms after 20 minutes; nothing is ever
purchased — commitments always go back to the person.
"""
import asyncio
import base64
import json
import os
import re
import time

import httpx
from aiohttp import WSMsgType, web

API = os.environ.get("SCOUT_API_URL", "http://api:8000")
WORKER_TOKEN = os.environ["SUPERAPP_WORKER_TOKEN"]
ANTHROPIC_KEY = os.environ.get("SUPERAPP_ANTHROPIC_API_KEY", "")
SESSION_TOKEN = os.environ.get("SUPERAPP_SCOUT_SESSION_TOKEN", "")
MODEL = os.environ.get("SCOUT_MODEL", "claude-opus-5")
H = {"Authorization": f"Bearer {WORKER_TOKEN}"}
PROFILE_DIR = "/profile"

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
MARKET_SYSTEM = (
    "You are Nano's scout reading a Facebook Marketplace results/listing page "
    "(the person's own logged-in session). From the page text, extract the "
    "REAL listings that match the errand: title, price, location, url (from "
    "the links list), and a one-line why. Never invent listings. If the page "
    "looks like a login wall or checkpoint, say so in caveats and return an "
    "empty shortlist."
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
MARKET_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "shortlist": STEP_SCHEMA["properties"]["shortlist"],
        "caveats": {"type": "string"},
    },
    "required": ["summary", "shortlist", "caveats"],
    "additionalProperties": False,
}

# ---- remote login window ----------------------------------------------------

remote = {"armed_until": 0.0, "page": None}


def _remote_ok(token: str) -> bool:
    return (SESSION_TOKEN and token == SESSION_TOKEN
            and time.time() < remote["armed_until"] and remote["page"] is not None)


REMOTE_HTML = """<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0;background:#08070E;color:#B9B4CC;font-family:system-ui}
#c{width:100vw;touch-action:manipulation}#bar{display:flex;gap:8px;padding:10px}
input{flex:1;background:#14101F;border:1px solid #444;border-radius:10px;color:#F4F2FA;padding:10px}
button{background:#C3B4FF;color:#17131F;border:0;border-radius:10px;padding:10px 14px;font-weight:600}</style>
<div id=bar><input id=t placeholder="Type here, then Send"><button onclick="sendText()">Send</button>
<button onclick="sendKey('Enter')">⏎</button></div>
<img id=c>
<script>
const tok=location.pathname.split('/').pop();
const ws=new WebSocket((location.protocol==='https:'?'wss':'ws')+'://'+location.host+'/scout/ws/'+tok);
const img=document.getElementById('c');let W=1,Hh=1;
ws.onmessage=(e)=>{const m=JSON.parse(e.data);if(m.frame){img.src='data:image/jpeg;base64,'+m.frame;W=m.w;Hh=m.h;}};
img.onclick=(e)=>{const r=img.getBoundingClientRect();
ws.send(JSON.stringify({type:'tap',x:(e.clientX-r.left)/r.width*W,y:(e.clientY-r.top)/r.height*Hh}));};
function sendText(){const v=document.getElementById('t').value;if(v)ws.send(JSON.stringify({type:'text',value:v}));
document.getElementById('t').value='';}
function sendKey(k){ws.send(JSON.stringify({type:'key',key:k}))}
</script>"""


async def remote_page_handler(request: web.Request) -> web.Response:
    if not _remote_ok(request.match_info["token"]):
        return web.Response(status=404, text="No login window is open. Ask Nano to connect first.")
    return web.Response(text=REMOTE_HTML, content_type="text/html")


async def remote_ws_handler(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    if not _remote_ok(request.match_info["token"]):
        await ws.close()
        return ws
    page = remote["page"]
    cdp = await page.context.new_cdp_session(page)

    async def on_frame(params):
        try:
            await ws.send_json({"frame": params["data"],
                                "w": params["metadata"]["deviceWidth"],
                                "h": params["metadata"]["deviceHeight"]})
            await cdp.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
        except Exception:  # noqa: BLE001
            pass

    cdp.on("Page.screencastFrame", lambda p: asyncio.create_task(on_frame(p)))
    await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 55,
                                            "maxWidth": 480, "maxHeight": 1000,
                                            "everyNthFrame": 2})
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT or not _remote_ok(request.match_info["token"]):
                break
            m = json.loads(msg.data)
            if m["type"] == "tap":
                await cdp.send("Input.dispatchMouseEvent", {
                    "type": "mousePressed", "x": m["x"], "y": m["y"],
                    "button": "left", "clickCount": 1})
                await cdp.send("Input.dispatchMouseEvent", {
                    "type": "mouseReleased", "x": m["x"], "y": m["y"],
                    "button": "left", "clickCount": 1})
            elif m["type"] == "text":
                await cdp.send("Input.insertText", {"text": m["value"]})
            elif m["type"] == "key" and m["key"] == "Enter":
                for t in ("rawKeyDown", "keyUp"):
                    await cdp.send("Input.dispatchKeyEvent", {
                        "type": t, "key": "Enter", "code": "Enter",
                        "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
    finally:
        try:
            await cdp.send("Page.stopScreencast")
            await cdp.detach()
        except Exception:  # noqa: BLE001
            pass
    return ws


# ---- research (1a, unchanged behavior, now async) ---------------------------

def search_web(query: str) -> list[dict]:
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


async def read_page(context, url: str) -> str:
    page = await context.new_page()
    try:
        await page.route("**/*", lambda route: asyncio.create_task(route.abort())
                         if route.request.resource_type in ("image", "media", "font")
                         else asyncio.create_task(route.continue_()))
        await page.goto(url, timeout=25000, wait_until="domcontentloaded")
        await page.wait_for_timeout(1500)
        text = await page.evaluate("document.body ? document.body.innerText : ''")
        return re.sub(r"\n{3,}", "\n\n", text)[:7000]
    except Exception as exc:  # noqa: BLE001
        return f"[could not load {url}: {type(exc).__name__}]"
    finally:
        await page.close()


def _llm(messages, schema, system):
    from anthropic import Anthropic

    client = Anthropic(api_key=ANTHROPIC_KEY)
    resp = client.messages.create(
        model=MODEL, max_tokens=2000,
        system=[{"type": "text", "text": system,
                 "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"format": {"type": "json_schema", "schema": schema}},
    )
    return "".join(b.text for b in resp.content if b.type == "text")


async def run_research(context, instruction: str) -> dict:
    if not ANTHROPIC_KEY:
        return {"summary": f"(stub) Scouted for: {instruction[:80]}",
                "shortlist": [], "caveats": "stub mode"}
    transcript = [{"role": "user", "content": json.dumps({"errand": instruction, "step": 0})}]
    for step in range(12):
        text = await asyncio.to_thread(_llm, transcript, STEP_SCHEMA, SYSTEM)
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
            observation = {"search_results": await asyncio.to_thread(search_web, move["query"])}
        else:
            observation = {"page_text": await read_page(context, move["url"])}
        transcript.append({"role": "user", "content": json.dumps(
            {"step": step + 1, "observation": observation}, ensure_ascii=False)[:9000]})
    return {"summary": "Ran out of steps before a confident shortlist.",
            "shortlist": [], "caveats": "hit the step budget; try a narrower errand"}


# ---- marketplace on the logged-in profile (1b) ------------------------------

async def run_marketplace(context, instruction: str) -> dict:
    query = instruction[:120]
    page = await context.new_page()
    try:
        await page.goto("https://www.facebook.com/marketplace/search/?query="
                        + httpx.URL("http://x", params={"q": query}).params["q"].replace(" ", "%20"),
                        timeout=35000, wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        body = await page.evaluate("document.body ? document.body.innerText : ''")
        links = await page.evaluate(
            """[...document.querySelectorAll('a[href*="/marketplace/item/"]')]
               .slice(0, 25).map(a => ({href: a.href.split('?')[0],
                                        text: (a.innerText || '').slice(0, 160)}))""")
        if "log in" in body.lower()[:2000] and not links:
            return {"summary": "Facebook wants a login — the session isn't connected yet.",
                    "shortlist": [],
                    "caveats": "Say 'connect facebook' and finish the login window first."}
        if not ANTHROPIC_KEY:
            return {"summary": "(stub) marketplace read", "shortlist": [], "caveats": "stub"}
        text = await asyncio.to_thread(
            _llm,
            [{"role": "user", "content": json.dumps(
                {"errand": instruction, "page_text": body[:6000], "links": links},
                ensure_ascii=False)}],
            MARKET_SCHEMA, MARKET_SYSTEM)
        parsed = json.loads(text)
        return {"summary": parsed["summary"][:400], "shortlist": parsed["shortlist"][:6],
                "caveats": parsed["caveats"][:300]}
    except Exception as exc:  # noqa: BLE001
        return {"summary": "Marketplace read failed.", "shortlist": [],
                "caveats": f"{type(exc).__name__}: {exc}"[:250]}
    finally:
        await page.close()


# ---- main loop --------------------------------------------------------------

async def handle_task(context, task: dict) -> dict:
    kind = task.get("kind", "research")
    instruction = task["instruction"]
    if kind == "connect_login" or instruction.strip().lower().startswith("connect "):
        site = instruction.split()[-1].lower()
        url = {"facebook": "https://www.facebook.com/login",
               "marketplace": "https://www.facebook.com/login"}.get(site, f"https://{site}")
        page = await context.new_page()
        await page.goto(url, timeout=35000, wait_until="domcontentloaded")
        remote["page"] = page
        remote["armed_until"] = time.time() + 20 * 60
        return {"summary": f"Login window is open for {site} — you have 20 minutes.",
                "shortlist": [], "caveats": "one-time; the session sticks after login",
                "connect": True}
    if kind == "marketplace" or "marketplace" in instruction.lower():
        return await run_marketplace(context, instruction)
    return await run_research(context, instruction)


async def poll_loop(context):
    print("scout up; polling", API)
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            try:
                r = await client.get(f"{API}/v1/tasks/next", headers=H)
                task = r.json().get("task") if r.status_code == 200 else None
            except httpx.HTTPError:
                task = None
            if not task:
                await asyncio.sleep(12)
                continue
            print("task", task["id"], task.get("kind"), task["instruction"][:80])
            try:
                result = await handle_task(context, task)
                await client.post(f"{API}/v1/tasks/{task['id']}/complete",
                                  headers=H, json={"result": result})
                print("done", task["id"])
            except Exception as exc:  # noqa: BLE001
                await client.post(f"{API}/v1/tasks/{task['id']}/fail",
                                  headers=H, json={"error": str(exc)[:500]})
                print("failed", task["id"], exc)


async def main():
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            PROFILE_DIR, headless=True,
            viewport={"width": 420, "height": 900},
            user_agent=("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) "
                        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile Safari/604.1"),
            args=["--no-sandbox", "--disable-dev-shm-usage"])
        app = web.Application()
        app.router.add_get("/scout/r/{token}", remote_page_handler)
        app.router.add_get("/scout/ws/{token}", remote_ws_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", 7900).start()
        await poll_loop(context)


if __name__ == "__main__":
    asyncio.run(main())
