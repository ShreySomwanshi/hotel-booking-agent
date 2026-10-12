"""Offline tests of the tool layer (no API key needed):  python tests.py"""
import os
import tempfile
import unittest
from datetime import date, timedelta

import db
import tools

TODAY = date.today()
def D(n): return (TODAY + timedelta(days=n)).isoformat()


class HotelTests(unittest.TestCase):
    def setUp(self):
        fd, self.path = tempfile.mkstemp(suffix=".db"); os.close(fd)
        os.environ["HOTEL_OUTBOX"] = tools.OUTBOX = tempfile.mkdtemp()
        db.init_db(self.path, TODAY)
        self.conn = db.connect(self.path)

    def tearDown(self):
        self.conn.close(); os.remove(self.path)

    def check(self, ci, co, bed, bf, guests=1):
        return db.check_availability(self.conn, D(ci), D(co), bed, bf, guests, TODAY)

    def test_exact_match(self):
        r = self.check(20, 22, "double", True, 2)
        self.assertEqual(r["status"], "exact")
        self.assertTrue(all(x["bed"] == "Double" and x["breakfast_included"] for x in r["rooms"]))
        self.assertEqual(r["rooms"][0]["total_price"], r["rooms"][0]["price_per_night"] * 2)

    def test_alternatives_when_exact_unavailable(self):
        for room in (102, 203, 207):  # every free single+breakfast room (108 is in maintenance)
            db.create_booking(self.conn, room, D(15), D(17), "X", "x@y.com", 1, TODAY)
        r = self.check(15, 17, "single", True, 1)
        self.assertEqual(r["status"], "alternatives")
        self.assertLessEqual(len(r["alternatives"]), 2)
        first = r["alternatives"][0]
        self.assertEqual((first["bed"], first["breakfast_included"]), ("Single", False))  # closest
        self.assertTrue(all("why_suggested" in a for a in r["alternatives"]))

    def test_fully_booked_suggests_dates(self):
        r = self.check(30, 32, "double", False, 2)
        self.assertEqual(r["status"], "none")
        self.assertIn("fully booked", r["reason"])
        self.assertTrue(r["suggested_dates"])
        self.assertTrue(r["suggested_dates"][0]["exact_match"])

    def test_party_size_too_big(self):
        r = self.check(20, 22, "double", True, 6)
        self.assertEqual(r["status"], "none")

    def test_invalid_dates(self):
        self.assertEqual(self.check(-3, -1, "single", False)["status"], "error")   # past
        self.assertEqual(self.check(5, 5, "single", False)["status"], "error")     # zero nights
        self.assertEqual(self.check(6, 5, "single", False)["status"], "error")     # reversed
        bad = db.check_availability(self.conn, "next friday", D(3), "single", False, 1, TODAY)
        self.assertEqual(bad["status"], "error")
        self.assertEqual(self.check(5, 6, "triple", False)["status"], "error")

    def test_preference_change_gives_fresh_result(self):
        a = self.check(20, 22, "single", False)
        b = self.check(20, 22, "double", True)
        self.assertNotEqual(a["rooms"][0]["room_no"], b["rooms"][0]["room_no"])

    def test_booking_blocks_double_booking_and_email(self):
        first = db.create_booking(self.conn, 104, D(20), D(22), "Asha", "asha@example.com", 2, TODAY)
        self.assertEqual(first["status"], "confirmed")
        second = db.create_booking(self.conn, 104, D(21), D(23), "Ravi", "ravi@example.com", 2, TODAY)
        self.assertEqual(second["status"], "error")
        # back-to-back stay (check-in on the previous guest's checkout day) is allowed
        third = db.create_booking(self.conn, 104, D(22), D(24), "Ravi", "ravi@example.com", 2, TODAY)
        self.assertEqual(third["status"], "confirmed")
        out = tools.dispatch("send_confirmation_email", {"booking_id": first["booking_id"]}, db_path=self.path)
        self.assertEqual(out["status"], "sent")
        self.assertTrue(os.path.exists(out["outbox_file"]))

    def test_booking_validation(self):
        r = db.create_booking(self.conn, 104, D(20), D(22), "Asha", "not-an-email", 2, TODAY)
        self.assertEqual(r["status"], "error")

    def test_rate_card(self):
        self.assertTrue(db.rate_card(self.conn))


if __name__ == "__main__":
    unittest.main(verbosity=2)
