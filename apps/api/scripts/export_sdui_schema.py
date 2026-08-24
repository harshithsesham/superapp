"""Export the SDUI contract as JSON Schema for the mobile app.

Run from apps/api:  python scripts/export_sdui_schema.py
"""
import json
from pathlib import Path

from superapp.sdui.blocks import Screen

out = Path(__file__).resolve().parents[3] / "packages" / "sdui-schema" / "schema.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(Screen.model_json_schema(), indent=2) + "\n")
print(f"wrote {out}")
