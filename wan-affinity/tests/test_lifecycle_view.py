import unittest
from base import Clock, dump, make_engine, make_packet, make_state
from wan_affinity.reconcile import read_lifecycle, read_dump


class LifecycleViewTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock(1000)
        self.engine=make_engine(clock=self.clock)
        self.packet=make_packet("tuple-a")
        self.admitted=self.engine.admission_decision(self.packet)

    def view(self,u=(),c=(),started=None):
        return read_lifecycle(True,dump(u),True,dump(c),
                              self.clock() if started is None else started)

    def test_never_confirmed_finished_flow_cleans(self):
        self.clock.advance(.1)
        self.engine.apply_reconcile(self.view())
        self.assertEqual(len(self.engine.tick()),1)
        self.assertFalse(self.engine.states)

    def test_held_unconfirmed_packet_retains_source(self):
        self.clock.advance(4)
        state=make_state("tuple-a",self.packet.src,self.packet.dst,wan=self.admitted.wan)
        self.engine.apply_reconcile(self.view(u=[state]))
        self.assertEqual(self.engine.tick(),[])
        self.assertFalse(self.engine.uncertain)
        self.assertIn(self.packet.src,self.engine.assoc)

    def test_confirmed_only_absence_is_not_terminal_evidence(self):
        self.clock.advance(4)
        self.engine.apply_reconcile(read_dump(True,dump([]),self.clock()))
        self.assertEqual(self.engine.tick(),[])
        self.assertTrue(self.engine.uncertain)

    def test_bad_unconfirmed_read_prevents_cleanup(self):
        self.clock.advance(1)
        result=read_lifecycle(False,None,True,dump([]),self.clock())
        self.engine.apply_reconcile(result)
        self.assertTrue(self.engine.uncertain)
        self.assertIn("tuple-a",self.engine.states)

    def test_truncated_confirmed_read_prevents_cleanup(self):
        self.clock.advance(1)
        result=read_lifecycle(True,dump([]),True,"",self.clock())
        self.engine.apply_reconcile(result)
        self.assertTrue(self.engine.uncertain)
        self.assertIn("tuple-a",self.engine.states)

    def test_older_empty_worker_result_keeps_new_admission(self):
        self.clock.advance(1)
        self.engine.apply_reconcile(self.view(started=999))
        self.assertIn("tuple-a",self.engine.states)
        self.assertEqual(self.engine.tick(),[])

    def test_old_same_tuple_row_cannot_confirm_newer_admission(self):
        state=make_state("tuple-a",self.packet.src,self.packet.dst,wan=1-self.admitted.wan)
        self.clock.advance(1)
        self.engine.apply_reconcile(self.view(c=[state],started=999))
        self.assertTrue(self.engine.states["tuple-a"].provisional)
        self.assertEqual(self.engine.states["tuple-a"].wan,self.admitted.wan)

    def test_stale_worker_view_is_rejected(self):
        self.clock.advance(30)
        self.engine.apply_reconcile(self.view(started=1001))
        self.assertTrue(self.engine.uncertain)
        self.assertIn("tuple-a",self.engine.states)

    def test_u_to_c_migration_deduplicates_and_keeps_nat_attribution(self):
        u=make_state("tuple-a",self.packet.src,self.packet.dst,wan=None)
        c=make_state("tuple-a",self.packet.src,self.packet.dst,wan=self.admitted.wan)
        result=self.view(u=[u],c=[c])
        self.assertTrue(result.valid and result.covers_unconfirmed)
        self.assertEqual(len(result.states),1)
        self.assertEqual(result.states[0].wan,self.admitted.wan)
