from satprof_calibrator.sources.igra import parse_igra_lines

def test_igra_parser_minimal():
    header = '#USM00000001 2026 01 02 00 0000   ncdc-gts ncdc-gts  450000  3000000'
    header = header.ljust(80)
    def level(p,t,rh,z,etime,wdir=270,wspd=100):
        s=[' ']*60
        s[0]='1'; s[1]='0'; s[3:8]=f'{etime:5d}'; s[9:15]=f'{p:6d}'; s[16:21]=f'{z:5d}'; s[22:27]=f'{t:5d}'; s[28:33]=f'{rh:5d}'; s[34:39]=f'{0:5d}'; s[40:45]=f'{wdir:5d}'; s[46:51]=f'{wspd:5d}'
        return ''.join(s)
    lines=[header,level(100000,150,800,100,0),level(50000,-250,400,5500,60),level(10000,-550,100,16000,120)]
    profiles=parse_igra_lines(lines)
    assert len(profiles)==1
    assert profiles[0].pressure_hpa[0]==1000
    assert len(profiles[0].pressure_hpa)==3
