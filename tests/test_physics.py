import numpy as np
from satprof_calibrator.physics import haversine_km, wind_components, rh_to_specific_humidity, specific_humidity_to_rh

def test_haversine():
    assert haversine_km(0,0,0,1) == pytest.approx(111.2, rel=0.01)

def test_humidity_roundtrip():
    p=np.array([1000,850,500]); t=np.array([290,280,250]); rh=np.array([70,50,30])
    q=rh_to_specific_humidity(p,t,rh); out=specific_humidity_to_rh(p,t,q)
    assert np.allclose(rh,out,rtol=1e-6)

import pytest
