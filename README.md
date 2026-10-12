# Hotel Aurora – Booking Agent

A conversational booking assistant for one hotel. Claude handles the dialogue; a SQLite database and a set of
tools handle the facts (availability, prices, bookings). The LLM never invents rooms or prices.

## Files

| File | Role |
|---|---|
| `db.py` | Schema, seed data (22 rooms + date-range bookings), availability check, suggestion logic, booking, rate card |
| `tools.py` | Tool schemas shown to the LLM + `dispatch()` that runs them; mock email (writes to `outbox/`) |
| `agent.py` | Chat loop, system prompt, tool-use loop |
| `tests.py` | Offline tests of every scenario (no API key needed) |

## Architecture

```
Guest ⇄ agent.py (chat loop) ⇄ Claude API  ── tool_use ──▶ tools.dispatch() ──▶ db.py ──▶ SQLite (hotel.db)
                                    ◀─────── tool_result (JSON) ────────────────┘
                                                                  └─▶ outbox/BK-XXXX.txt (mock email)
```

**How the LLM and the tools interact**

1. `agent.py` prints a fixed greeting (the exact question from the brief) and seeds it into the history.
2. Each guest message goes to Claude along with the system prompt and four tool definitions.
3. Claude extracts the slots (dates, bed, breakfast, guests) and emits a `check_availability` tool call. The
   system prompt requires this on **every** room request or preference change, so a mid-chat change
   ("actually, double with breakfast") simply triggers a new call with fresh arguments.
4. `tools.dispatch()` runs the call against SQLite and returns JSON with a `status`. The loop feeds it back and Claude
   writes the reply from that data only:

| `status` | Meaning | Agent behaviour |
|---|---|---|
| `exact` | bed + breakfast + capacity match | Present room no., type, price/night, breakfast, stay total; ask for name + email |
| `alternatives` | no exact match, but other rooms are free | Offer the 1–2 closest with a `why_suggested` reason; guest picks or declines |
| `none` | nothing free (or party too large) | Explain why and offer the nearest dates (`suggested_dates`) |
| `error` | past/invalid dates, bad bed type, etc. | Explain and ask for corrected input |

5. After the guest picks a room and gives name + email, Claude calls `create_booking`, then
   `send_confirmation_email(booking_id)`. Both are mocked-but-real: the booking is a database row and the email is a
   text file in `outbox/`.

Other tools: `get_rates` answers "how much?" before the guest has given dates.

## Database

`rooms(room_no, room_type, bed_type, breakfast, price, floor, max_guests, status)` and
`bookings(booking_id, room_no, check_in, check_out, guest_name, email, guests, status)`.

The sample table has one `Booked` flag, but real availability depends on the dates requested. So `Booked` is modelled
as date-range rows in `bookings` (seeded to mirror the sample: 102 and 202 are booked around today), and `rooms.status`
holds `Available` / `Maintenance`. A room is free for a stay iff it is `Available` and no confirmed booking overlaps
`[check_in, check_out)`. Back-to-back stays (check-in on someone's checkout day) are allowed. Seed data also includes
a "conference weekend" 30–33 days out where the whole hotel is full, to demo the sold-out path.

## Suggestion logic (`db._alternatives`)

When no room matches exactly, candidates (free for the dates and big enough for the party) are ranked by a mismatch cost:
bed-type mismatch = 2, breakfast mismatch = 1, then lowest price. The top two with **different** (bed, breakfast)
combinations are returned, e.g. for "single + breakfast": (1) single, no breakfast; (2) double, with breakfast.
If nothing at all is free, `_nearest_dates` scans ±14 days for the same length of stay and returns the two nearest
windows, preferring exact matches. So the agent never has to answer with a bare "no rooms available".

## Edge cases covered

- **Fully booked** → reason + nearest alternative dates
- **Past / reversed / malformed dates, >30 nights, bad bed type, bad guest count** → `error` with a human-readable message
- **Preference change mid-conversation** → prompt rule + a fresh tool call each time (tested)
- **Price before committing** → quoted from tool results, or `get_rates` if no dates yet
- **Party too large for any free room** → `none` with an explicit reason
- **Double-booking race** → `create_booking` re-checks inside a `BEGIN IMMEDIATE` transaction
- **Invalid email / missing name** → rejected by the tool, so the agent re-asks

## Run it

```bash
pip install -r requirements.txt
python tests.py                       # offline: tests every scenario against the tool layer
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py --verbose             # chat; --verbose prints each tool call and result
python agent.py --reset-db            # reseed (seed bookings are relative to today's date)
```

Optional env vars: `HOTEL_AGENT_MODEL` (default `claude-sonnet-4-6`), `HOTEL_DB`, `HOTEL_OUTBOX`.

Scenarios to try (dates are relative to the day you run it):

- **Exact:** "Next month on the 10th for 2 nights, double bed with breakfast, 2 guests."
- **Alternatives:** book out 102/203/207 for a date range, or ask for a single + breakfast on a busy date.
- **No availability:** ask for a stay 30–32 days from today.
- **Edge cases:** "Check in yesterday"; "what's the price of a deluxe room?"; change bed type after seeing options.

## Limitations / next steps

Single hotel, no payments or cancellation, email is mocked, no auth or persistence of the chat itself.
Natural next steps: cancellation/modification tools, real SMTP, per-date pricing, and a web front end.
