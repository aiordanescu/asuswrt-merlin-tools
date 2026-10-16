import random
import unittest
from base import Clock, INTERNET, INTERNET2, dump, make_engine, make_packet, make_state, read_dump
from wan_affinity.reconcile import read_lifecycle
from wan_affinity.selection import AffinitySelector, StickySelector


class MixedAdoptionTests(unittest.TestCase):
    def test_permanent_mixed_sessions_never_block_either_mode(self):
        for selector in (StickySelector(random.Random(9)), AffinitySelector('mixed-test', (2, 1))):
            with self.subTest(mode=selector.name):
                clock = Clock(1000)
                engine = make_engine(selector=selector, clock=clock)
                src = '192.168.1.91'
                old = [make_state('old-a', src, INTERNET, wan=0),
                       make_state('old-b', src, INTERNET2, wan=1),
                       make_state('old-u', src, INTERNET2, wan=None)]

                def view(states):
                    return read_lifecycle(True, dump([]), True, dump(states), clock())

                engine.reconstruct(view(old))
                choices = [engine.admission_decision(make_packet('new-'+str(i), src=src, proto='udp'))
                           for i in range(32)]
                self.assertEqual({r.action for r in choices}, {'mark'})
                self.assertEqual(len({r.wan for r in choices}), 1)
                self.assertEqual([engine.states[s.ctid].wan for s in old], [0, 1, None])
                self.assertNotIn(src, engine.transitional)
                members = [make_state('new-'+str(i), src, INTERNET, proto='udp', wan=choices[0].wan)
                           for i in range(32)]
                clock.advance(1)
                engine.reconstruct(view(old+members))
                self.assertEqual(engine.tick(), [])
                clock.advance(1)
                engine.reconstruct(view(old))
                self.assertEqual(len(engine.tick()), 1)
                engine.reconstruct(view(old))
                self.assertNotIn(src, engine.assoc)
                self.assertEqual(engine.admission_decision(make_packet('later', src=src)).action, 'mark')

    def test_unknown_health_does_not_invent_an_eligible_wan(self):
        engine = make_engine()
        src = '192.168.1.93'
        engine.reconstruct(read_dump(True, dump([make_state('old', src, INTERNET, wan=None)]), engine.clock()))
        for fact in engine.health.firmware.values():
            fact.route_ready = None
        self.assertEqual(engine.admission_decision(make_packet('new', src=src)).action, 'pass')
        self.assertNotIn(src, engine.assoc)
