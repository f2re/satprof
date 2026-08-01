from satprof_calibrator.demo import generate_demo
from satprof_calibrator.storage import Workspace

def test_demo_pipeline(tmp_path):
    result=generate_demo(Workspace(tmp_path),n_soundings=50,seed=3)
    assert result['collocations'] >= 40
    assert result['pairs'] >= 300
    assert result['report'].exists()
