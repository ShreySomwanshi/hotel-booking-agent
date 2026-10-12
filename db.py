"""Room database + availability / suggestion / booking logic (no LLM in here).

Tables
  rooms     room_no, room_type, bed_type, breakfast, price, floor, max_guests, status
            status is the room's operational state: 'Available' | 'Maintenance'
  bookings  booking_id, room_no, check_in, check_out, guest_name, email, guests, status
            Date-range reservations. A room is free for a stay if it is 'Available'
            AND has no confirmed booking overlapping the requested dates.

(The assignment's sample table has a single 'Booked' flag. Real availability depends on
the dates asked for, so 'Booked' is modelled as rows in `bookings`, seeded below to
reproduce the sample: e.g. rooms 102 and 202 are booked around today.)
"""
import os
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta

DB_PATH = os.environ.get("HOTEL_DB", os.path.join(os.path.dirname(__file__), "hotel.db"))
MAX_NIGHTS = 30
MAX_ADVANCE_DAYS = 365

# room_no, type, bed, breakfast, price/night (INR), floor, max_guests, status
ROOMS = [
    (101, "Standard", "Single", 0, 2200, 1, 1, "Available"),
    (102, "Standard", "Single", 1, 2600, 1, 1, "Available"),
    (103, "Standard", "Double", 0, 3000, 1, 2, "Available"),
    (104, "Standard", "Double", 1, 3400, 1, 2, "Available"),
    (105, "Standard", "Single", 0, 2200, 1, 1, "Available"),
    (106, "Standard", "Double", 0, 3000, 1, 2, "Available"),
    (107, "Standard", "Double", 1, 3400, 1, 2, "Available"),
    (108, "Standard", "Single", 1, 2600, 1, 1, "Maintenance"),
    (109, "Standard", "Double", 0, 3000, 1, 2, "Available"),
    (201, "Deluxe", "Double", 1, 4500, 2, 3, "Available"),
    (202, "Deluxe", "Double", 0, 4100, 2, 3, "Available"),
    (203, "Deluxe", "Single", 1, 3800, 2, 2, "Available"),
    (204, "Deluxe", "Double", 1, 4500, 2, 3, "Available"),
    (205, "Deluxe", "Double", 0, 4100, 2, 3, "Available"),
    (206, "Deluxe", "Single", 0, 3400, 2, 2, "Available"),
    (207, "Deluxe", "Single", 1, 3800, 2, 2, "Available"),
    (208, "Deluxe", "Double", 1, 4500, 2, 3, "Available"),
    (209, "Deluxe", "Single", 0, 3400, 2, 2, "Available"),
    (301, "Suite", "Double", 1, 6500, 3, 4, "Available"),
    (302, "Suite", "Double", 1, 6500, 3, 4, "Available"),
    (303, "Suite", "Double", 0, 6000, 3, 4, "Available"),
    (305, "Suite", "Double", 1, 7200, 3, 4, "Available"),
]

SCHEMA = """
DROP TABLE IF EXISTS bookings;
DROP TABLE IF EXISTS rooms;
CREATE TABLE rooms (
  room_no INTEGER PRIMARY KEY, room_type TEXT, bed_type TEXT, breakfast INTEGER,
  price INTEGER, floor INTEGER, max_guests INTEGER, status TEXT
);
CREATE TABLE bookings (
  booking_id TEXT PRIMARY KEY, room_no INTEGER REFERENCES rooms(room_no),
  check_in TEXT, check_out TEXT, guest_name TEXT, email TEXT, guests INTEGER,
  status TEXT DEFAULT 'Confirmed', created_at TEXT
);
CREATE INDEX idx_bookings_room ON bookings(room_no, check_in, check_out);
"""


def connect(path=None):
    conn = sqlite3.connect(path or DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.isolation_level = None  # we manage transactions explicitly
    return conn


def init_db(path=None, today=None):
    """(Re)create and seed the database. Seed bookings are relative to `today`."""
    today = today or date.today()
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?)", ROOMS)

    def d(n):
        return (today + timedelta(days=n)).isoformat()

    # (room, start_offset, end_offset): mirrors the sample 'Booked' rooms + variety
    seed = [(102, -1, 10), (202, 0, 7), (101, 3, 6), (103, 5, 9), (104, 2, 4),
            (201, 1, 5), (203, 4, 8), (301, 0, 3), (207, 2, 6), (302, 8, 12)]
    for room, a, b in seed:
        _insert(conn, room, d(a), d(b), "(seed)", "seed@example.com", 1)
    # Conference weekend: whole hotel full -> demos the "fully booked" path
    for r in ROOMS:
        if r[7] == "Available":
            _insert(conn, r[0], d(30), d(33), "(conference)", "seed@example.com", 1)
    conn.close()


def _insert(conn, room_no, ci, co, name, email, guests):
    bid = "BK-" + uuid.uuid4().hex[:8].upper()
    conn.execute(
        "INSERT INTO bookings(booking_id,room_no,check_in,check_out,guest_name,email,guests,created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (bid, room_no, ci, co, name, email, guests, datetime.now().isoformat(timespec="seconds")))
    return bid


# ---------------------------------------------------------------- validation
def parse_stay(check_in, check_out, today=None):
    today = today or date.today()
    try:
        ci = date.fromisoformat(str(check_in))
        co = date.fromisoformat(str(check_out))
    except ValueError:
        raise ValueError("Dates must be valid calendar dates in YYYY-MM-DD format.")
    if ci < today:
        raise ValueError(f"Check-in {ci} is in the past (today is {today}).")
    if co <= ci:
        raise ValueError("Check-out must be after check-in.")
    if (co - ci).days > MAX_NIGHTS:
        raise ValueError(f"Maximum stay is {MAX_NIGHTS} nights.")
    if (ci - today).days > MAX_ADVANCE_DAYS:
        raise ValueError("We can only take bookings up to a year in advance.")
    return ci, co


def _norm_bed(bed):
    b = str(bed).strip().lower()
    if b not in ("single", "double"):
        raise ValueError("Bed type must be 'single' or 'double'.")
    return b.capitalize()


# ---------------------------------------------------------------- core search
def _free_rooms(conn, ci, co):
    rows = conn.execute(
        """SELECT * FROM rooms r WHERE r.status = 'Available' AND NOT EXISTS (
             SELECT 1 FROM bookings b WHERE b.room_no = r.room_no AND b.status = 'Confirmed'
             AND b.check_in < :co AND b.check_out > :ci)
           ORDER BY price, room_no""", {"ci": ci.isoformat(), "co": co.isoformat()}).fetchall()
    return [dict(r) for r in rows]


def _view(r, nights):
    return {"room_no": r["room_no"], "type": r["room_type"], "bed": r["bed_type"],
            "breakfast_included": bool(r["breakfast"]), "price_per_night": r["price"],
            "nights": nights, "total_price": r["price"] * nights,
            "floor": r["floor"], "max_guests": r["max_guests"]}


def _reason(r, bed, breakfast):
    has_bf = bool(r["breakfast"])
    bed_ok, bf_ok = r["bed_type"] == bed, has_bf == breakfast
    bf_txt = "breakfast included" if has_bf else "breakfast not included"
    if bed_ok and not bf_ok:
        return f"Same {bed.lower()} bed, but {bf_txt}"
    if bf_ok and not bed_ok:
        return f"{r['bed_type']} bed instead of {bed.lower()} ({r['room_type']} room), {bf_txt} as requested"
    return f"{r['bed_type']} bed, {r['room_type']} room, {bf_txt}"


def _split(free, bed, breakfast, guests):
    fit = [r for r in free if r["max_guests"] >= guests]
    exact = [r for r in fit if r["bed_type"] == bed and bool(r["breakfast"]) == breakfast]
    others = [r for r in fit if r not in exact]
    return fit, exact, others


def _alternatives(others, bed, breakfast, limit=2):
    """Rank by closeness: bed mismatch costs more than breakfast mismatch, then price.
    Prefer variety: the 2nd alternative must differ in (bed, breakfast) from the 1st."""
    def cost(r):
        return ((r["bed_type"] != bed) * 2 + (bool(r["breakfast"]) != breakfast), r["price"])
    ranked = sorted(others, key=cost)
    picked, seen = [], set()
    for r in ranked:
        key = (r["bed_type"], r["breakfast"])
        if key not in seen:
            picked.append(r)
            seen.add(key)
        if len(picked) == limit:
            break
    return picked


def check_availability(conn, check_in, check_out, bed_type, breakfast, guests=1, today=None):
    """The one tool the agent calls for every room request.
    Returns status: 'exact' | 'alternatives' | 'none' | 'error'."""
    today = today or date.today()
    try:
        ci, co = parse_stay(check_in, check_out, today)
        bed = _norm_bed(bed_type)
        guests = int(guests)
        if guests < 1 or guests > 6:
            raise ValueError("Number of guests must be between 1 and 6.")
    except (ValueError, TypeError) as e:
        return {"status": "error", "error": str(e)}

    breakfast = bool(breakfast)
    nights = (co - ci).days
    free = _free_rooms(conn, ci, co)
    fit, exact, others = _split(free, bed, breakfast, guests)
    base = {"check_in": ci.isoformat(), "check_out": co.isoformat(), "nights": nights,
            "requested": {"bed": bed, "breakfast": breakfast, "guests": guests}}

    if exact:
        return {**base, "status": "exact", "rooms": [_view(r, nights) for r in exact[:3]]}

    if others:
        alts = []
        for r in _alternatives(others, bed, breakfast):
            v = _view(r, nights)
            v["why_suggested"] = _reason(r, bed, breakfast)
            alts.append(v)
        return {**base, "status": "alternatives", "alternatives": alts}

    # Nothing usable: say why, and offer the nearest dates that work.
    out = {**base, "status": "none",
           "reason": ("rooms are free but none can fit this many guests" if free
                      else "hotel is fully booked for these dates"),
           "suggested_dates": _nearest_dates(conn, ci, co, bed, breakfast, guests, today)}
    return out


def _nearest_dates(conn, ci, co, bed, breakfast, guests, today, limit=2, window=14):
    n = (co - ci).days
    found = []
    for shift in range(1, window + 1):
        for sign in (1, -1):
            nci = ci + timedelta(days=sign * shift)
            if nci < today:
                continue
            nco = nci + timedelta(days=n)
            _, exact, others = _split(_free_rooms(conn, nci, nco), bed, breakfast, guests)
            pick = exact[:1] or _alternatives(others, bed, breakfast, 1)
            if pick:
                found.append({"check_in": nci.isoformat(), "check_out": nco.isoformat(),
                              "exact_match": bool(exact), "example_room": _view(pick[0], n)})
            if len(found) == limit:
                return found
    return found


# ---------------------------------------------------------------- booking
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def create_booking(conn, room_no, check_in, check_out, guest_name, email, guests=1, today=None):
    """Re-checks availability inside a write transaction so two guests can't take the same room."""
    today = today or date.today()
    try:
        ci, co = parse_stay(check_in, check_out, today)
    except ValueError as e:
        return {"status": "error", "error": str(e)}
    if not guest_name or not str(guest_name).strip():
        return {"status": "error", "error": "Guest name is required."}
    if not EMAIL_RE.match(str(email or "")):
        return {"status": "error", "error": "A valid email address is required."}

    conn.execute("BEGIN IMMEDIATE")
    try:
        room = next((r for r in _free_rooms(conn, ci, co) if r["room_no"] == int(room_no)), None)
        if room is None:
            conn.execute("ROLLBACK")
            return {"status": "error", "error": f"Room {room_no} is not available for those dates."}
        if room["max_guests"] < int(guests):
            conn.execute("ROLLBACK")
            return {"status": "error", "error": f"Room {room_no} sleeps at most {room['max_guests']}."}
        bid = _insert(conn, room["room_no"], ci.isoformat(), co.isoformat(),
                      guest_name.strip(), email.strip(), int(guests))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    nights = (co - ci).days
    return {"status": "confirmed", "booking_id": bid, "guest_name": guest_name.strip(),
            "email": email.strip(), "check_in": ci.isoformat(), "check_out": co.isoformat(),
            "guests": int(guests), **_view(room, nights)}


def get_booking(conn, booking_id):
    row = conn.execute(
        "SELECT b.*, r.room_type, r.bed_type, r.breakfast, r.price FROM bookings b "
        "JOIN rooms r USING(room_no) WHERE booking_id = ?", (booking_id,)).fetchone()
    return dict(row) if row else None


def rate_card(conn):
    """Prices without dates, for 'how much does it cost?' before the guest commits."""
    rows = conn.execute(
        "SELECT room_type, bed_type, breakfast, MIN(price) lo, MAX(price) hi, MAX(max_guests) g "
        "FROM rooms WHERE status='Available' GROUP BY room_type, bed_type, breakfast "
        "ORDER BY lo").fetchall()
    return [{"type": r["room_type"], "bed": r["bed_type"], "breakfast_included": bool(r["breakfast"]),
             "price_per_night_from": r["lo"], "price_per_night_to": r["hi"], "max_guests": r["g"]}
            for r in rows]
