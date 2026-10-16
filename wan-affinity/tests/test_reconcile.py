import unittest

from base import (
    Clock,
    INTERNET,
    dump,
    make_engine,
    make_packet,
    make_state,
    miscounted,
    partial_line,
    truncated,
)
from wan_affinity.reconcile import parse_dump, read_dump

SRC = "192.168.1.66"


class ReconcileParseTests(unittest.TestCase):
    def test_valid_empty_dump_is_valid(self):
        self.assertEqual(parse_dump(dump([])), [])

    def test_truncated_dump_rejected(self):
        self.assertIsNone(parse_dump(truncated([])))

    def test_miscounted_dump_rejected(self):
        self.assertIsNone(parse_dump(miscounted([make_state("c1", SRC, INTERNET, wan=0)])))

    def test_garbage_row_rejected(self):
        self.assertIsNone(parse_dump(partial_line([make_state("c1", SRC, INTERNET, wan=0)])))

    def test_read_error_rejected(self):
        result = read_dump(False, None, 1000.0)
        self.assertFalse(result.valid)
        self.assertEqual(result.error, "read-error")

    def test_invalid_read_retains_association(self):
        clock = Clock(1000.0)
        engine = make_engine(clock=clock, idle_seconds=0)
        engine.admission_decision(make_packet("c1", src=SRC))
        clock.advance(10.0)
        engine.apply_reconcile(read_dump(True, truncated([]), clock()))
        self.assertEqual(engine.tick(), [])
        self.assertIn(SRC, engine.assoc)


if __name__ == "__main__":
    unittest.main()
