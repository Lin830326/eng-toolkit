"""Independent four-point periodic counter and ASTM example cycle tables."""

from collections import Counter
import json
import math

import numpy as np
import pytest

from eng_toolkit.domains.fatigue.rainflow import rainflow
from tests.fatigue_testing import RF, call, close, ok, table, v


def cycles(result):
    out=Counter()
    for row in table(result):
        out[(round(float(row["range_MPa"]),8),round(float(row["mean_MPa"]),8))]+=float(row["weighted_count"])
    return out


def four_point_periodic(values):
    """Independent circular four-point deletion, deliberately quadratic for tiny test histories."""
    values=list(values)
    if values[0]==values[-1]:
        values.pop()
    changed=True
    while changed and len(values)>2:
        changed=False
        for i in range(len(values)):
            a,b,c=values[i-1],values[i],values[(i+1)%len(values)]
            if (b-a)*(c-b)>=0:
                values.pop(i)
                changed=True
                break
    found=Counter()
    while len(values)>2:
        for i in range(len(values)):
            a,b,c,d=[values[(i+j)%len(values)] for j in range(4)]
            if abs(b-c)<=abs(a-b) and abs(b-c)<=abs(c-d):
                found[(abs(b-c),(b+c)/2)]+=1
                remove={(i+1)%len(values),(i+2)%len(values)}
                values=[v for j,v in enumerate(values) if j not in remove]
                break
        else:
            pytest.fail("reference counter found no removable four-point cycle")
    if len(values)==2:
        found[(abs(values[1]-values[0]),sum(values)/2)]+=1
    return found


def test_half_cycle_residue_and_variation_identity():
    r=ok(rainflow,**RF)
    # 0->100 gives half range100 mean50; 100->-100 half200 mean0; -100->0 half100 mean-50.
    assert cycles(r)==Counter({(100,50):.5,(200,0):.5,(100,-50):.5})
    close(v(r,"full_cycles"),0)
    close(v(r,"half_cycles"),3)
    close(v(r,"range_count_sum"),200)  # (100+200+100)/2


def test_astm_e1049_figure_6_history():
    # E1049 Fig.6/Table X1.3: -2,1,-3,5,-1,3,-4,4,-2; ranges 3,4,4,8,9,8,6,
    # the nested (-1,3) range is one full cycle; all other entries are half cycles.
    history=[-2,1,-3,5,-1,3,-4,4,-2]
    r=ok(rainflow,history=json.dumps(history),sample_scale="1 MPa")
    expected=Counter({(3,-.5):.5,(4,-1):.5,(4,1):1,(8,1):.5,(9,.5):.5,(8,0):.5,(6,1):.5})
    assert cycles(r)==expected
    close(v(r,"weighted_cycles"),4)
    close(v(r,"range_count_sum"),23)  # total variation=3+4+8+6+4+7+8+6=46


def test_random_periodic_differential_rotation_and_conservation():
    rng=np.random.default_rng(1049)
    for _ in range(32):
        history=rng.integers(-100,101,16).tolist()
        if max(history)==min(history):
            continue
        expected=four_point_periodic(history)
        r=ok(rainflow,history=json.dumps(history),sample_scale="1 MPa",closed=True,range_bins=7,mean_bins=5)
        assert cycles(r)==expected
        close(v(r,"half_cycles"),0)
        variation=sum(abs(history[(i+1)%len(history)]-a) for i,a in enumerate(history))
        close(v(r,"range_count_sum"),variation/2)
        shift=int(rng.integers(1,len(history)))
        rotated=history[shift:]+history[:shift]
        other=ok(rainflow,history=json.dumps(rotated),sample_scale="1 MPa",closed=True)
        assert cycles(other)==expected
        close(sum(r.data["range_histogram"]["counts"]),v(r,"weighted_cycles"))
        close(sum(map(sum,r.data["range_mean_matrix"]["counts"])),v(r,"weighted_cycles"))


def test_damage_repeat_and_equivalent_range():
    args=dict(history="[100,-100,100]",sample_scale="1 MPa",closed=True,basquin_a="1000 MPa",basquin_b=-.1,reference_life="100 cycle")
    r=ok(rainflow,**args,repeats=7)
    # A/S=10, m=10 -> N=10^10, one full cycle -> D=1e-10; repeats scale damage only.
    close(v(r,"damage_per_block"),1e-10)
    close(v(r,"damage"),7e-10)
    close(v(r,"repeats_to_failure"),1e10)
    close(v(r,"equivalent_range"),2*1000*(100/1e-10)**-.1)


@pytest.mark.parametrize("below,expected",[("none",0),("elementary",1/4e6),("haibach",1/8e6)])
def test_below_knee_modes(below,expected):
    # A=100000, b=-.5 -> Se=100 at Nk=1e6; Sa=50 -> N=Nk*2^2 or Nk*2^3.
    r=ok(rainflow,history="[50,-50,50]",sample_scale="1 MPa",closed=True,basquin_a="100000 MPa",
         basquin_b=-.5,endurance_limit="100 MPa",below_knee=below)
    close(v(r,"damage_per_block"),expected)
    if below=="none":
        assert r.data["repeats_to_failure"] is None


def test_mean_goodman_cycle_and_two_point_curve():
    # Cycle 0..200 -> Sa=100, Sm=100. Su=500 -> equivalent125. A=800,b=log10(.5)/3.
    r=ok(rainflow,history="[0,200,0]",sample_scale="1 MPa",closed=True,sn_points="400 MPa,1000;200 MPa,1000000",
         mean_method="goodman",ultimate_strength="500 MPa")
    expected_life=(125/800)**(3/math.log10(.5))
    close(v(r,"damage_per_block"),1/expected_life)


def test_csv_job_input_and_force_only(tmp_path):
    p=tmp_path/"history.csv"
    p.write_text("0,0\n1,1\n2,-1\n3,0\n",encoding="utf-8")
    r=ok(rainflow,history_file=str(p),sample_scale="1 ksi")
    assert len(r.provenance.input_files)==1
    close(v(r,"range_count_sum","ksi"),2)
    r=ok(rainflow,history="[0,1,-1,0]",history_kind="force",sample_scale="1 kN")
    close(v(r,"range_count_sum","N"),2000)


def test_hysteresis_suppresses_small_interior_excursion():
    r=ok(rainflow,history="[0,10,9,11,0]",sample_scale="1 MPa",hysteresis="2 MPa")
    # Small 10->9->11 excursion is swallowed; remaining 0->11->0 has TV22.
    close(v(r,"range_count_sum"),11)
    close(v(r,"weighted_cycles"),1)


@pytest.mark.parametrize("args",[
    dict(history="[1]"),dict(history="[1,1,1]"),dict(history="[0,NaN]"),dict(history="[0,1e308]"),
    dict(history="[0,true,1]"),dict(history="[[0],1]"),dict(history="[0,1e-400]"),
    dict(history="[0,1]",sample_scale="1e-300 yPa"),
    dict(history="[0,1]",basquin_a="100 MPa",sn_points="100 MPa,1000;50 MPa,100000"),
    dict(history="[0,1]",range_bins=257),dict(history="[0,1]",history_file="absent.csv"),
])
def test_rainflow_refusals(args):
    assert not call(rainflow,**{**RF,**args}).ok


def test_a_logger_header_line_is_skipped_and_reported(tmp_path):
    # UAT M2-2: 'time_s,strain_ue' on line 1 gave "expected a finite decimal literal" at /history_file/0.
    plain = tmp_path/"plain.csv"
    plain.write_text("0.0,0\n0.1,1\n0.2,-1\n0.3,0\n",encoding="utf-8")
    headed = tmp_path/"headed.csv"
    headed.write_text("time_s,strain_ue\n0.0,0\n0.1,1\n0.2,-1\n0.3,0\n",encoding="utf-8")
    a=ok(rainflow,history_file=str(plain),sample_scale="1 MPa")
    b=ok(rainflow,history_file=str(headed),sample_scale="1 MPa")
    assert b.data["header_skipped"]=="time_s,strain_ue" and a.data["header_skipped"] is None
    close(v(b,"range_count_sum"),v(a,"range_count_sum"))
    one=tmp_path/"one.csv"
    one.write_text("\ufeffstrain\n0\n1\n-1\n0\n",encoding="utf-8")  # a one-column file with a BOM and a header
    c=ok(rainflow,history_file=str(one),sample_scale="1 MPa")
    assert c.data["header_skipped"]=="strain"
    close(v(c,"range_count_sum"),v(a,"range_count_sum"))


@pytest.mark.parametrize("text,line",[
    ("strain\n0\n1\nabc\n0\n",4),  # a word among the numbers
    ("time_s,strain\ntext,more\n0,1\n",2),  # a second header-like line
    ("0,1\ntime,strain\n1,2\n",2),  # a header only counts on line 1
    ("time_s,strain\n0,1,2\n1,2,3\n",2),  # three columns
    ("time_s,strain\n0,1\n0,2\n",3),  # time not increasing
    ("1,abc\n2,3\n",1),  # mixed numbers and words on line 1
    ("__import__('os')\n1\n2\n",1),
    ("nan\n1\n2\n",1),
])
def test_garbage_is_still_refused_and_the_message_says_line_n(tmp_path,text,line):
    p=tmp_path/"bad.csv"
    p.write_text(text,encoding="utf-8")
    r=call(rainflow,history_file=str(p),sample_scale="1 MPa")
    assert r.ok is False and r.error.family=="input.invalid" and r.error.details["param"].startswith("/history_file")
    assert f"line {line}" in r.error.message, r.error
