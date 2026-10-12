"""Hotel booking chat agent: Claude (tool use) + SQLite availability tool.

Run:  export ANTHROPIC_API_KEY=...   then   python agent.py [--verbose] [--reset-db]
"""
import argparse
import json
import os
import sys
from datetime import date

import db
import tools

MODEL = os.environ.get("HOTEL_AGENT_MODEL", "claude-sonnet-4-6")

GREETING = ("Hello, and welcome to Hotel Aurora! What dates would you like to stay, and do you "
            "prefer a single or double bed? Would you like breakfast included?")

SYSTEM = """You are the booking assistant for Hotel Aurora. Today's date is {today} ({weekday}). \
The guest has already been greeted with: "{greeting}"

Collect: check-in date, check-out date, bed type (single/double), breakfast (yes/no), number of guests. \
Ask only for what is still missing, one or two things at a time. Convert relative dates ("this Friday", \
"next weekend") to YYYY-MM-DD using today's date; if a date is ambiguous, confirm it.

Rules
1. Never state room numbers, availability or prices from memory. Call check_availability EVERY time the guest \
asks for a room or changes dates, bed type, breakfast or guest count - including mid-conversation changes.
2. status 'exact': present the room (room no., type, bed, price/night, breakfast, total for the stay) and ask \
for the guest's full name and email to finalize.
3. status 'alternatives': say plainly that the exact combination is not free, then offer the 1-2 alternatives \
with the reason given and prices. Let the guest choose one or decline. Never book an alternative unless they pick it.
4. status 'none': never answer with a flat "no rooms". Explain why (reason) and offer suggested_dates if present; \
otherwise invite them to try other dates.
5. status 'error': explain the problem kindly (e.g. past date) and ask for corrected input.
6. If asked about price before committing, answer from tool results; if no dates yet, call get_rates. Prices are \
in Indian rupees (use the Rs / ₹ symbol with thousands separators).
7. Only call create_booking after the guest has chosen a specific room and given name + email, and said yes to booking. \
Then call send_confirmation_email with the booking_id, and tell the guest the booking ID and that a confirmation \
email was sent.
8. Be concise, warm, and professional. Stay on hotel booking topics."""


def run_turn(client, messages, system, verbose=False):
    """One user turn: loop until the model produces a final text reply (it may call tools several times)."""
    while True:
        resp = client.messages.create(model=MODEL, max_tokens=1024, system=system,
                                      tools=tools.TOOLS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content if b.type == "text")
        results = []
        for block in resp.content:
            if block.type == "tool_use":
                out = tools.dispatch(block.name, block.input)
                if verbose:
                    print(f"  [tool] {block.name}({json.dumps(block.input)})\n         -> {json.dumps(out)[:400]}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out)})
        messages.append({"role": "user", "content": results})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true", help="show tool calls and results")
    ap.add_argument("--reset-db", action="store_true", help="recreate and reseed the database")
    args = ap.parse_args()

    if args.reset_db or not os.path.exists(db.DB_PATH):
        db.init_db()
        print(f"(database seeded at {db.DB_PATH})")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("Set ANTHROPIC_API_KEY first (or run `python tests.py` to test the tool logic offline).")

    import anthropic
    client = anthropic.Anthropic()
    today = date.today()
    system = SYSTEM.format(today=today.isoformat(), weekday=today.strftime("%A"), greeting=GREETING)
    messages = [{"role": "user", "content": "(The guest just opened the chat.)"},
                {"role": "assistant", "content": GREETING}]

    print(f"\nHotel Aurora: {GREETING}\n(type 'quit' to exit)\n")
    while True:
        try:
            user = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if user.lower() in ("quit", "exit"):
            break
        if not user:
            continue
        messages.append({"role": "user", "content": user})
        print(f"\nHotel Aurora: {run_turn(client, messages, system, args.verbose)}\n")


if __name__ == "__main__":
    main()
