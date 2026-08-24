"""Golden-set eval for the nutrition agent's meal estimation.

Run BEFORE changing the estimation prompt or model, and after:

    cd apps/api && PYTHONPATH=. python scripts/run_golden_nutrition.py

Reads golden/nutrition/manifest.json (copy manifest.example.json and add ~10 real
meal photos). Requires SUPERAPP_ANTHROPIC_API_KEY — in stub mode this only checks
the pipeline shape, not accuracy. Exits non-zero on any failed case.
"""
import base64
import json
import sys
from pathlib import Path

from superapp.agents.nutrition import ESTIMATE_SYSTEM, MEAL_SCHEMA
from superapp.config import get_settings
from superapp.db import Base, SessionLocal, engine
from superapp.llm.provider import LLMProvider

GOLDEN_DIR = Path(__file__).resolve().parents[1] / "golden" / "nutrition"
MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}


def main() -> int:
    manifest_path = GOLDEN_DIR / "manifest.json"
    if not manifest_path.exists():
        print(f"No {manifest_path} — copy manifest.example.json and add photos.")
        return 1
    if not get_settings().anthropic_api_key:
        print("WARNING: stub mode (no API key) — checking pipeline shape only, not accuracy.\n")

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    provider = LLMProvider()
    cases = json.loads(manifest_path.read_text())["cases"]
    failures = 0

    for case in cases:
        photo = GOLDEN_DIR / case["photo"]
        data = photo.read_bytes()
        media_type = MIME[photo.suffix.lstrip(".").lower()]
        resp = provider.complete(
            db, user_id="golden", agent="nutrition", task="estimate",
            system=ESTIMATE_SYSTEM, prompt="Estimate the meal in the photo.",
            images=[(media_type, base64.standard_b64encode(data).decode())],
            schema=MEAL_SCHEMA,
        )
        try:
            est = json.loads(resp.text)
        except json.JSONDecodeError:
            est = {}

        problems = []
        for field, (lo, hi) in case.get("expect", {}).items():
            got = est.get(field)
            if got is None or not (lo <= got <= hi):
                problems.append(f"{field}={got} not in [{lo}, {hi}]")
        status = "FAIL" if problems else "ok  "
        failures += bool(problems)
        print(f"{status} {case['photo']:<20} {case.get('label', '')!r:<28} "
              f"kcal={est.get('kcal')} ${resp.cost_usd:.4f}"
              + (f"  ({'; '.join(problems)})" if problems else ""))

    db.rollback()
    db.close()
    print(f"\n{len(cases) - failures}/{len(cases)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
