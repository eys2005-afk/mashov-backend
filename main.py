from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
import os
import re
from typing import Optional

app = FastAPI(title="Mashov Dashboard Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin123")

# In-memory cache (persists as long as server is running)
memory_cache = {"events": [], "source_url": ""}

def extract_doc_id(url: str) -> Optional[str]:
    match = re.search(r"/document/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None

def fetch_doc_text(doc_id: str) -> str:
    export_url = f"https://docs.google.com/document/d/{doc_id}/export?format=txt"
    resp = requests.get(export_url, timeout=15)
    if resp.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Google Docs returned {resp.status_code}")
    return resp.text

def parse_with_claude(doc_text: str) -> list:
    prompt = """אתה מפרש לוח שנה של בית ספר.
קיבלת טבלה של לוח שנה שנתי. כל תא מכיל תאריך עברי, תאריך לועזי, ואירוע.
המשימה שלך: חלץ את כל האירועים והמבחנים מהטבלה והחזר JSON בלבד.
חוקים:
- תאריך בפורמט DD/MM/YYYY (לועזי)
- type: אחד מ: בוחן / חג / טיול / אירוע / אסיפה
- אם יש מבחן במקצוע (מתמטיקה, תנ"ך, אנגלית וכו') → type = בוחן
- חגים ידועים (ראש השנה, יום כיפור, פסח וכו') → type = חג
- טיולים ומסעות → type = טיול
- אסיפת הורים → type = אסיפה
- שאר האירועים → type = אירוע
- התעלם מימים ריקים
- החזר ONLY JSON array, ללא שום טקסט נוסף
פורמט:
[
  {"date": "23/09/2025", "title": "ראש השנה", "type": "חג"},
  {"date": "19/10/2025", "title": "בוחן במשנה", "type": "בוחן"}
]
הטבלה:
""" + doc_text[:8000]

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4000,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=60,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Claude API error: {response.text}")
    raw = response.json()["content"][0]["text"].strip()
    raw = re.sub(r"^```json\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    return json.loads(raw)


class RefreshRequest(BaseModel):
    doc_url: str
    secret: str


@app.post("/refresh")
def refresh_events(body: RefreshRequest):
    if body.secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Unauthorized")
    doc_id = extract_doc_id(body.doc_url)
    if not doc_id:
        raise HTTPException(status_code=400, detail="Invalid Google Doc URL")
    doc_text = fetch_doc_text(doc_id)
    events = parse_with_claude(doc_text)
    memory_cache["events"] = events
    memory_cache["source_url"] = body.doc_url
    return {"ok": True, "count": len(events), "events": events}


@app.get("/events")
def get_events():
    return {
        "ok": True,
        "count": len(memory_cache["events"]),
        "events": memory_cache["events"]
    }


@app.get("/health")
def health():
    return {"status": "ok", "events_loaded": len(memory_cache["events"])}
