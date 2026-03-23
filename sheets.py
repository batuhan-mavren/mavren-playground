"""
Google Sheets logger for Mavren Playground.

Appends a row to the configured Google Sheet after every analysis.
Images are hosted on the playground itself (not Drive).

Required env vars:
    GOOGLE_SERVICE_ACCOUNT_JSON  — full JSON content of the service account key
    GOOGLE_SHEET_ID              — the spreadsheet ID (from the sheet URL)

Expected sheet columns (row 1 header):
    Timestamp | Image (Drive link) | Channel | Funnel Stage | Region |
    Primary Emotion | Valence | Arousal | Coherence | Synthesis | Full Response (JSON)
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("mavren.sheets")

# Lazy-init globals
_gc = None  # gspread client
_sheet = None  # gspread Worksheet


def _get_credentials():
    """Parse the service account JSON from env var."""
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        return None

    from google.oauth2.service_account import Credentials

    info = json.loads(raw)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    return Credentials.from_service_account_info(info, scopes=scopes)


def _get_gspread_client():
    """Lazy-init the gspread client."""
    global _gc
    if _gc is None:
        creds = _get_credentials()
        if creds is None:
            return None
        import gspread
        _gc = gspread.authorize(creds)
    return _gc


def _get_sheet():
    """Lazy-init the worksheet (first sheet of the configured spreadsheet)."""
    global _sheet
    if _sheet is None:
        gc = _get_gspread_client()
        if gc is None:
            return None
        sheet_id = os.getenv("GOOGLE_SHEET_ID", "")
        if not sheet_id:
            logger.warning("GOOGLE_SHEET_ID not set — sheet logging disabled")
            return None
        spreadsheet = gc.open_by_key(sheet_id)
        _sheet = spreadsheet.sheet1
    return _sheet


def append_row(
    image_link: Optional[str],
    channel: str,
    funnel_stage: str,
    region: Optional[str],
    raw_response: dict,
    synthesis: Optional[str] = None,
):
    """
    Append a row to the Google Sheet.

    Columns: Timestamp | Image (Drive link) | Channel | Funnel Stage | Region |
             Primary Emotion | Valence | Arousal | Coherence | Synthesis | Full Response (JSON)
    """
    try:
        sheet = _get_sheet()
        if sheet is None:
            logger.info("Sheet not configured — skipping row append")
            return

        # Extract key metrics from the raw response
        emotion_state = raw_response.get("emotion_state", {})
        coherence = raw_response.get("coherence", {})

        row = [
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            image_link or "—",
            channel,
            funnel_stage,
            region or "—",
            emotion_state.get("primary", "—"),
            str(emotion_state.get("valence", "—")),
            str(emotion_state.get("arousal", "—")),
            str(coherence.get("overall", "—") if isinstance(coherence, dict) else "—"),
            synthesis or "—",
            json.dumps(raw_response, default=str),
        ]

        sheet.append_row(row, value_input_option="USER_ENTERED")
        logger.info("Appended row to Google Sheet")

    except Exception as e:
        logger.exception("Failed to append row to sheet: %s", e)
