import sys
import unittest
from pathlib import Path
import numpy as np
import pandas as pd
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from evaluation import evaluate, merge_alerts, reference_periodic, read_scores
from experiments import inject


class EventEvaluationTest(unittest.TestCase):
    def test_one_hour_is_inclusive(self):
        self.assertEqual(len(merge_alerts([0,3_600_000,7_200_001],[True]*3)),2)

    def test_duplicate_and_outside_events_are_false_positives(self):
        ts=np.arange(12)*3_600_000
        flags=np.array([1,0,1,0,0,1,0,0,1,0,0,0],dtype=bool)
        windows=[{"start_ms":0,"end_ms":3*3_600_000},{"start_ms":7*3_600_000,"end_ms":9*3_600_000}]
        m,e=evaluate(ts,flags,windows,0,12*3_600_000,3_600_000)
        self.assertEqual((m["tp"],m["fp"],m["fn"],m["duplicate_fp"],m["outside_fp"]),(2,2,0,1,1))
        self.assertAlmostEqual(m["f1"],2/3)
        self.assertAlmostEqual(m["outside_days"],5/24)

    def test_no_reassignment_at_split_boundary(self):
        ts=np.arange(5)*1_800_000
        m,e=evaluate(ts,[0,1,1,1,0],[{"start_ms":3_600_000,"end_ms":7_200_000}],3_600_000,9_000_000,1_800_000)
        self.assertEqual((m["tp"],m["fp"],m["fn"]),(0,0,1))
        self.assertEqual(e,[])

    def test_closed_window_last_point_included(self):
        m,_=evaluate([0,10,20],[0,0,1],[{"start_ms":10,"end_ms":20}],0,30,10,gap_ms=1)
        self.assertEqual(m["tp"],1)
        self.assertEqual(m["windows"][0]["first_alert_ms"],20)

    def test_empty_alerts_have_zero_not_nan(self):
        m,_=evaluate([0,10],[0,0],[],0,20,10)
        self.assertEqual((m["precision"],m["recall"],m["f1"]),(0,0,0))

    def test_overlapping_truth_and_unsorted_time_rejected(self):
        with self.assertRaises(ValueError):
            merge_alerts([2,1],[1,1])
        with self.assertRaises(ValueError):
            evaluate([0,10],[1,1],[{"start_ms":0,"end_ms":10},{"start_ms":10,"end_ms":20}],0,20,10)


class ArtifactContractTest(unittest.TestCase):
    def test_injection_accepts_integer_values_without_modifying_original(self):
        ts=np.arange(40*48,dtype=np.int64)*1_800_000
        source=pd.DataFrame({"timestamp_ms":ts,"source_timestamp":"kept", "value":np.ones(len(ts),dtype=int)*11})
        splits={"validation":{"start_ms":int(ts[10*48]),"end_ms_exclusive":int(ts[-1])+1_800_000}}
        changed,windows=inject(source,splits,48)
        self.assertTrue((source.value==11).all())
        self.assertTrue((changed.iloc[:10*48].value==11).all())
        self.assertEqual(len(windows),6)
        self.assertTrue(any(changed.value%1!=0))

    def test_frozen_rows_may_have_no_state_update(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"scores.csv"
            path.write_text("timestamp_ms,value,baseline,residual,residual_center,scale,score,flag,update,phase\n0,10,10,0,0,1,0,false,,0\n")
            self.assertEqual(len(read_scores(path)),1)


class IndependentReferenceTest(unittest.TestCase):
    def cfg(self):
        return dict(period=2,k=2,window=2,step_ms=10,origin_ms=0,epsilon="auto",tau=4,clip=3,strategy="bounded")

    def test_three_phase_warmup_and_constant(self):
        r=reference_periodic(np.arange(10)*10,np.ones(10)*5,self.cfg())
        self.assertEqual(r.timestamp_ms.tolist(),[60,70,80,90])
        np.testing.assert_array_equal(r.score,[0]*4)
        np.testing.assert_array_equal(r.scale,[1e-9]*4)

    def test_future_does_not_modify_prefix(self):
        vals=[1,2,2,3,1,1,4,2,9,1,1000,-999]
        prefix=reference_periodic(np.arange(10)*10,vals[:10],self.cfg())
        full=reference_periodic(np.arange(12)*10,vals,self.cfg())
        np.testing.assert_array_equal(prefix.score,full.score[:len(prefix)])

    def test_gap_uses_timestamp_phase(self):
        r=reference_periodic([0,10,20,30,40,50,70,80],[10,20,10,20,10,20,20,10],self.cfg())
        self.assertEqual(r.phase.tolist(),[1,0])
        self.assertTrue((r.score==0).all())

if __name__=="__main__":unittest.main()
