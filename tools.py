"""Tool schemas exposed to the LLM + the dispatcher that executes them."""
import os
from datetime import date, datetime

import db

OUTBOX = os.environ.get("HOTEL_OUTBOX", os.path.join(os.path.dirname(__file__), "outbox"))

TOOLS = [
    {
        "name": "check_availability",
        "description": (
            "Query the room database. MUST be called every time the guest asks for a room or "
            "changes dates, bed type, breakfast or guest count. Returns status 'exact' (matching "
            "rooms), 'alternatives' (closest 1-2 options), 'none' (nothing free; may include "
            "suggested_dates) or 'error' (invalid input, e.g. past dates)."),
        "input_schema": {
            "type": "object",
            "properties": {
                "check_in": {"type": "string", "description": "YYYY-MM-DD"},
                "check_out": {"type": "string", "description": "YYYY-MM-DD, after check_in"},
                "bed_type": {"type": "string", "enum": ["single", "double"]},
                "breakfast": {"type": "boolean"},
                "guests": {"type": "integer", "minimum": 1},
            },
            "required": ["check_in", "check_out", "bed_type", "breakfast", "guests"],
        },
    },
    {
        "name": "get_rates",
        "description": "Rate card (price per night by room type/bed/breakfast) with no dates. Use when "
                       "the guest asks about prices before giving dates.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "create_booking",
        "description": "Finalize a booking for a room returned by check_availability. Only call after the "
                       "guest has chosen a specific room AND given their name and email.",
        "input_schema": {
            "type": "object",
            "properties": {
                "room_no": {"type": "integer"},
                "check_in": {"type": "string"}, "check_out": {"type": "string"},
                "guest_name": {"type": "string"}, "email": {"type": "string"},
                "guests": {"type": "integer", "minimum": 1},
            },
            "required": ["room_no", "check_in", "check_out", "guest_name", "email", "guests"],
        },
    },
    {
        "name": "send_confirmation_email",
        "description": "Send the confirmation email for a booking_id returned by create_booking.",
        "input_schema": {"type": "object",
                         "properties": {"booking_id": {"type": "string"}},
                         "required": ["booking_id"]},
    },
]


def _send_email(conn, booking_id):
    b = db.get_booking(conn, booking_id)
    if not b:
        return {"status": "error", "error": f"Unknown booking {booking_id}"}
    nights = (date.fromisoformat(b["check_out"]) - date.fromisoformat(b["check_in"])).days
    body = (f"To: {b['email']}\nSubject: Booking confirmed - {booking_id}\n\n"
            f"Dear {b['guest_name']},\n\nYour stay at Hotel Aurora is confirmed.\n"
            f"  Booking ID : {booking_id}\n  Room       : {b['room_no']} ({b['room_type']}, {b['bed_type']} bed)\n"
            f"  Breakfast  : {'Included' if b['breakfast'] else 'Not included'}\n"
            f"  Dates      : {b['check_in']} to {b['check_out']} ({nights} nights)\n"
            f"  Total      : Rs {b['price'] * nights:,} (Rs {b['price']:,}/night)\n\n"
            f"We look forward to welcoming you.\n")
    os.makedirs(OUTBOX, exist_ok=True)       # mock: write to a file instead of SMTP
    path = os.path.join(OUTBOX, f"{booking_id}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"  [email logged -> {path}]")
    return {"status": "sent", "to": b["email"], "outbox_file": path}


def dispatch(name, args, today=None, db_path=None):
    """Run one tool call. Always returns a JSON-serialisable dict (errors included)."""
    conn = db.connect(db_path)
    try:
        if name == "check_availability":
            return db.check_availability(conn, today=today, **args)
        if name == "get_rates":
            return {"rates": db.rate_card(conn)}
        if name == "create_booking":
            return db.create_booking(conn, today=today, **args)
        if name == "send_confirmation_email":
            return _send_email(conn, args["booking_id"])
        return {"status": "error", "error": f"Unknown tool {name}"}
    except TypeError as e:  # missing / unexpected arguments from the model
        return {"status": "error", "error": f"Bad arguments: {e}"}
    finally:
        conn.close()
