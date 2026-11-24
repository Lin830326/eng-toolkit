"""Record refusals name the record kind, its number, what was received and the grammar with an example (round 3: S1-6 / S1-7, S4 spring supports).

Round 2, S1: ``structures_frame`` answered a point load with 7 fields only with "expected (6,) nonempty comma-separated fields", and the assistant
failed three times.  Every record parser of structures_* and structures3d_* shares ``records`` / ``fields`` / ``invalid``, so every refusal that
points into a record carries the same note: ``[<kind> record N (line L): received '<clipped>'; expected <grammar>; e.g. '<example>']``.
"""

import pytest

from eng_toolkit.domains.structures import _common as c2
from eng_toolkit.domains.structures import frame as frame2
from eng_toolkit.domains.structures import modal as modal2
from eng_toolkit.domains.structures import truss as truss2
from eng_toolkit.domains.structures import section as section2
from eng_toolkit.domains.structures3d import _common as c3
from eng_toolkit.domains.structures3d import foundation as foundation3
from eng_toolkit.domains.structures3d import modal as modal3
from eng_toolkit.domains.structures3d import plate as plate3
from eng_toolkit.domains.structures3d import static as static3
from tests.structures_testing import call as call2
from tests.structures3d_testing import call as call3

NODES = "a,0 m,0 m;b,4 m,0 m;c,8 m,0 m"
MEMBERS = "e,a,b,200 GPa,1000 mm^2,1e6 mm^4;f,b,c,200 GPa,1000 mm^2,1e6 mm^4"
SUPPORTS = "a,pinned;c,roller_y"
NODES3 = "a,0 m,0 m,0 m;b,4 m,0 m,0 m;c,8 m,0 m,0 m"
MEMBERS3 = (
    "e,a,b,200 GPa,80 GPa,1e-3 m^2,1e-6 m^4,2e-6 m^4,1e-6 m^4;f,b,c,200 GPa,80 GPa,1e-3 m^2,1e-6 m^4,2e-6 m^4,1e-6 m^4"
)
SUPPORTS3 = "a,fixed"


def frame(**over):
    args = dict(nodes=NODES, members=MEMBERS, supports=SUPPORTS)
    args.update(over)
    return call2(frame2.frame, **args)


def refused(result, pointer):
    assert not result.ok
    assert result.error.family.split(".")[0] in ("input", "units"), result.error
    assert result.error.details["param"] == pointer, result.error.details
    return result.error.message


def note(message, kind, number, *needles):
    """The pieces every refusal carries."""
    assert f"{kind} record {number}" in message, message
    assert "received '" in message and "expected " in message and "e.g. '" in message, message
    for needle in needles:
        assert needle in message, (needle, message)


# ----------------------------------------------------------------------------- the failing input of round 2


def test_the_s1_member_point_load_with_seven_fields_shows_the_grammar():
    bad = "e,point,global,2 m,0 N,-11772 N,0 N*m"  # the exact record the assistant sent: 7 fields, the grammar has 6
    message = refused(frame(member_loads=bad), "/member_loads/0")
    note(message, "member_loads", 1, "got 7", "expected 6", "member,point,local|global,a,Fx,Fy", bad, "e,point,global,2 m,0 N,-5 kN")


def test_the_s1_member_point_load_with_five_fields():
    message = refused(frame(member_loads="e,point,global,2 m,-11772 N"), "/member_loads/0")
    note(message, "member_loads", 1, "got 5", "member,point,local|global,a,Fx,Fy")


def test_a_uniform_row_names_its_own_field_count():
    message = refused(frame(member_loads="e,uniform,local,0 N/m"), "/member_loads/0")
    note(message, "member_loads", 1, "expected 5", "got 4", "member,uniform,local|global,qx,qy")


# ----------------------------------------------------------------------------- every record kind of the 2D frame


@pytest.mark.parametrize(
    "param, bad, pointer, kind, number, grammar",
    [
        ("nodes", "a,0 m,0 m;b,4 m", "/nodes/1", "nodes", 2, "id,x,y"),
        ("members", "e,a,b,200 GPa,1000 mm^2", "/members/0", "members", 1, "id,start,end,E,A,I[,release]"),
        ("supports", "a,pinned;c", "/supports/1", "supports", 2, "node,spring,kx,ky,krot"),
        ("supports", "a,pinned;c,spring,1000 kN/m", "/supports/1", "supports", 2, "node,spring,kx,ky,krot[,x0,y0,rot0]"),
        ("loads", "b,0 N,-1 kN", "/loads/0", "loads", 1, "node,Fx,Fy,Mz"),
        ("loads", "b,0 N,-1 kN,0 N*m;b,5,6,7", "/loads/1/1", "loads", 2, "node,Fx,Fy,Mz"),
        ("member_loads", "e,uniform,local,1 N/m,2 N/m,3 N/m", "/member_loads/0", "member_loads", 1, "member,uniform,local|global,qx,qy"),
        ("member_loads", "e,distributed,local,1 N/m,2 N/m", "/member_loads/0/1", "member_loads", 1, "member,linear,local|global,qx0,qy0,qx1,qy1"),
        ("shear_properties", "e,80 GPa", "/shear_properties/0", "shear_properties", 1, "member,G,As"),
        ("thermal", "e,0.00001 1/K,50 K", "/thermal/0", "thermal", 1, "member,alpha,dT,dT/dy"),
    ],
)
def test_every_frame_record_kind_is_named_with_its_grammar(param, bad, pointer, kind, number, grammar):
    message = refused(frame(**{param: bad}), pointer)
    note(message, kind, number, grammar)


def test_the_s4_spring_support_with_the_3d_syntax_is_corrected_by_the_message():
    # round 1, S4: 'c,spring,ux,1000 kN/m,0 m' (the structures3d grammar) on the 2D tool
    message = refused(frame(supports="a,pinned;c,spring,ux,1000 kN/m,0 m"), "/supports/1/2")
    note(message, "supports", 2, "node,spring,kx,ky,krot[,x0,y0,rot0]", "c,spring,5 kN/m,0 N/m,0 N*m/rad")
    assert "N*m/rad" in message


def test_the_documented_spring_support_example_solves():
    result = frame(supports="a,pinned;c,spring,5 kN/m,5 kN/m,0 N*m/rad", loads="b,0 N,-1 kN,0 N*m")
    assert result.ok, result.error


def test_the_line_number_is_given_when_the_text_has_several_lines():
    message = refused(frame(loads="b,0 N,-1 kN,0 N*m\nb,0 N,-2 kN,0 N*m\nb,1 N,2 N"), "/loads/2")
    note(message, "loads", 3, "(line 3)")


def test_no_line_is_named_in_a_one_line_text_and_a_blank_line_counts_for_the_next_record_number():
    message = refused(frame(loads="b,0 N,-1 kN,0 N*m;b,1 N,2 N"), "/loads/1")
    assert "(line" not in message
    message = refused(frame(loads="b,0 N,-1 kN,0 N*m\n\nb,1 N,2 N,3 N"), "/loads/1")
    assert "loads record 2 (line 2)" in message and "empty or overlong record" in message


def test_what_was_received_is_clipped_and_cleaned():
    long = "b," + "9" * 400 + " N,1 N,1 N"
    message = refused(frame(loads=long), "/loads/0/1")
    assert "..." in message and len(message) < 900
    message = refused(frame(loads="b,1 N,2 N,3 N" + chr(7)), "/loads/0/3")
    assert chr(7) not in message


def test_a_refusal_outside_a_record_is_unchanged():
    result = call2(frame2.frame, nodes=NODES, members=MEMBERS, supports=SUPPORTS, stations=1)
    assert not result.ok and result.error.details["param"] == "/stations" and "record" not in result.error.message


def test_an_unknown_reference_lists_the_known_ids():
    message = refused(frame(loads="q,0 N,-1 kN,0 N*m"), "/loads/0/0")
    assert "unknown reference 'q'" in message and "known ids: a, b, c" in message


# ----------------------------------------------------------------------------- truss, modal, section


@pytest.mark.parametrize(
    "param, bad, pointer, kind, number, grammar",
    [
        ("nodes", "a,0 m", "/nodes/0", "nodes", 1, "id,x,y[,z]"),
        ("members", "e,a,b,200 GPa", "/members/0", "members", 1, "id,node_i,node_j,E,A[,I]"),
        ("supports", "a", "/supports/0", "supports", 1, "node,axes"),
        ("loads", "b,10 kN", "/loads/0", "loads", 1, "node,Fx,Fy[,Fz]"),
        ("thermal", "e,100 K", "/thermal/0", "thermal", 1, "member,dT,alpha"),
    ],
)
def test_every_truss_record_kind_is_named_with_its_grammar(param, bad, pointer, kind, number, grammar):
    args = dict(nodes="a,0 m,0 m;b,2 m,0 m", members="e,a,b,200 GPa,100 mm^2", supports="a,xy;b,y", loads="b,1 kN,0 N")
    args[param] = bad
    message = refused(call2(truss2.truss, **args), pointer)
    note(message, kind, number, grammar)


def test_modal_mass_records_name_their_grammar():
    args = dict(nodes=NODES, members=MEMBERS, supports=SUPPORTS, density="7800 kg/m^3")
    message = refused(call2(modal2.modal, **args, nodal_masses="b,10 kg"), "/nodal_masses/0")
    note(message, "nodal_masses", 1, "node,m,J | node,mx,my,J")
    message = refused(call2(modal2.modal, **args, member_masses="e"), "/member_masses/0")
    note(message, "member_masses", 1, "member,mass_per_length")


def test_a_polygon_row_names_its_ring():
    result = call2(section2.section, shape="polygon", polygon="0 mm,0 mm;10 mm,0 mm;10 mm", unit_system="metric")
    message = refused(result, "/polygon/2")
    note(message, "polygon", 3, "x,y")


# ----------------------------------------------------------------------------- 3D


def static(**over):
    args = dict(nodes=NODES3, members=MEMBERS3, supports=SUPPORTS3)
    args.update(over)
    return call3(static3.static, **args)


@pytest.mark.parametrize(
    "param, bad, pointer, kind, number, grammar",
    [
        ("nodes", "a,0 m,0 m", "/nodes/0", "nodes", 1, "id,x,y,z"),
        ("members", "e,a,b,200 GPa,80 GPa,1e-3 m^2", "/members/0", "members", 1, "id,start,end,E,G,A,Iy,Iz,J"),
        ("orientations", "e,roll", "/orientations/0", "orientations", 1, "member,roll,angle | member,vector,vx,vy,vz"),
        ("shear_properties", "e,800 mm^2", "/shear_properties/0", "shear_properties", 1, "member,Asy,Asz"),
        ("releases", "e,start", "/releases/0", "releases", 1, "member,start|end,rx+ry+rz"),
        ("supports", "a,fixed;b,spring,ux", "/supports/1", "supports", 2, "node,spring,axis,k[,rest]"),
        ("loads", "b,1 kN,0 N,0 N", "/loads/0", "loads", 1, "node,Fx,Fy,Fz,Mx,My,Mz"),
        ("member_loads", "e,point,global,1 m,0 N,1 kN", "/member_loads/0", "member_loads", 1, "member,point,local|global,a,Fx,Fy,Fz"),
        ("thermal", "e,12e-6 1/K", "/thermal/0", "thermal", 1, "member,alpha,dT"),
    ],
)
def test_every_3d_record_kind_is_named_with_its_grammar(param, bad, pointer, kind, number, grammar):
    message = refused(static(**{param: bad}), pointer)
    note(message, kind, number, grammar)


def test_3d_modal_and_the_point_load_tools_name_their_grammar():
    message = refused(call3(modal3.modal, nodes=NODES3, members=MEMBERS3, supports=SUPPORTS3, density="7800 kg/m^3", nodal_masses="b,10 kg"), "/nodal_masses/0")
    note(message, "nodal_masses", 1, "node,m,J | node,mx,my,mz,Jx,Jy,Jz")
    plate = call3(plate3.plate, length="1 m", width="1 m", thickness="10 mm", youngs_modulus="200 GPa", poisson_ratio=0.3, point_loads="0.4 m,0.6 m")
    message = refused(plate, "/point_loads/0")
    note(message, "point_loads", 1, "x,y,P")
    assert "'0.4 m,0.6 m,100 N'" in message


# ----------------------------------------------------------------------------- every example in a grammar is a record that parses


def test_every_2d_example_in_the_grammar_parses():
    from eng_toolkit.domains.structures._model import parse_frame, parse_nodes

    context = dict(nodes=NODES, members=MEMBERS, supports=SUPPORTS)
    seen = set()
    for kind, (forms, examples) in c2.GRAMMAR["*"].items():
        for example in examples:
            seen.add(kind)
            if kind == "nodes":
                parse_nodes(example)
            elif kind in ("polygon", "holes", "member_masses", "nodal_masses"):
                continue  # parsed by their own tools: covered through the tools below
            else:
                parse_frame(**{**context, kind: example})
    assert {"supports", "member_loads", "thermal", "shear_properties", "loads", "members", "nodes"} <= seen
    for example in c2.GRAMMAR["*"]["member_masses"][1]:
        assert call2(modal2.modal, nodes=NODES, members=MEMBERS, supports=SUPPORTS, member_masses=example, density="7800 kg/m^3").ok
    for example in c2.GRAMMAR["*"]["nodal_masses"][1]:
        assert call2(modal2.modal, nodes=NODES, members=MEMBERS, supports=SUPPORTS, nodal_masses=example, density="7800 kg/m^3").ok
    assert call2(section2.section, shape="polygon", polygon="0 mm,0 mm;10 mm,0 mm;10 mm,10 mm").ok


def test_every_truss_example_in_the_grammar_parses():
    base = dict(nodes="a,0 m,0 m;b,2 m,0 m", members="e,a,b,200 GPa,100 mm^2", supports="a,xy;b,y", loads="b,1 kN,0 N")
    for kind, (forms, examples) in c2.GRAMMAR["truss"].items():
        for example in examples:
            args = dict(base)
            args[kind] = example
            if kind == "nodes":
                args["nodes"] = example + ";b,2 m,0 m"
            if kind == "supports":  # the two examples hold different joints: one alone is a mechanism
                args["supports"] = example + (";b,y" if example == "a,xy" else "") + (";a,xy" if example == "b,y" else "")
            assert call2(truss2.truss, **args).ok, (kind, example)


def test_every_3d_example_in_the_grammar_parses():
    from eng_toolkit.domains.structures3d._model import parse_frame

    context = dict(nodes=NODES3, members=MEMBERS3, supports=SUPPORTS3)
    for kind, (forms, examples) in c3.GRAMMAR["*"].items():
        for example in examples:
            if kind == "nodes":
                continue  # the context itself
            if kind in ("member_masses", "nodal_masses"):
                assert call3(modal3.modal, nodes=NODES3, members=MEMBERS3, supports=SUPPORTS3, density="7800 kg/m^3", **{kind: example}).ok, (kind, example)
            elif kind == "members":
                parse_frame(**{**context, "nodes": "a,0 m,0 m,0 m;b,4 m,0 m,0 m", "members": example})
            elif kind == "supports":
                parse_frame(nodes="a,0 m,0 m,0 m;b,4 m,0 m,0 m;c,8 m,0 m,0 m;d,8 m,4 m,0 m", members=MEMBERS3 + ";g,c,d,200 GPa,80 GPa,1e-3 m^2,1e-6 m^4,2e-6 m^4,1e-6 m^4", supports=example)
            else:
                parse_frame(**{**context, kind: example})
    for example in c3.GRAMMAR["plate"]["point_loads"][1]:
        assert call3(plate3.plate, length="1 m", width="1 m", thickness="10 mm", youngs_modulus="200 GPa", poisson_ratio=0.3, point_loads=example).ok
    for example in c3.GRAMMAR["foundation"]["point_loads"][1]:
        assert call3(foundation3.foundation, length="10 m", flexural_rigidity="2e7 N*m^2", foundation_stiffness="1e6 N/m^2", point_loads=example).ok


# ----------------------------------------------------------------------------- the grammar text is also in the parameter descriptions


def test_the_frame_supports_description_documents_the_spring_record():
    text = c2.FrameSupports.__metadata__[0].description
    assert "c,spring,5 kN/m,0 N/m,0 N*m/rad" in text and "kx,ky,krot" in text and len(text) <= 140


# ----------------------------------------------------------------------------- truss: the applied load totals (S2 r2-1)

ROOF = dict(
    nodes="a,0 m,0 m;b,2 m,1 m;c,4 m,0 m;d,2 m,0 m",
    members="ab,a,b,200 GPa,500 mm^2;bc,b,c,200 GPa,500 mm^2;ad,a,d,200 GPa,500 mm^2;dc,d,c,200 GPa,500 mm^2;bd,b,d,200 GPa,500 mm^2",
    supports="a,xy;c,y",
)


def test_the_truss_reports_the_sum_of_the_applied_joint_loads_and_which_joints_carry_load():
    # a pressure resolved to joints by hand: the eave joints a and c take half a panel each, the ridge b a full panel
    full = call2(truss2.truss, **ROOF, loads="a,-1 kN,2 kN;b,0 N,4 kN;c,1 kN,2 kN")
    assert full.ok, full.error
    assert full.data["applied_total_fx"]["quantity"] == pytest.approx(0.0, abs=1e-9)
    assert full.data["applied_total_fy"]["quantity"] == pytest.approx(8000.0)
    assert full.data["loaded_nodes"] == ["a", "b", "c"]
    # round 2, S2: the eave joints were left out; the reactions still balance the loads, but the total is now short of the hand total (8 kN)
    short = call2(truss2.truss, **ROOF, loads="b,0 N,4 kN")
    assert short.ok and short.data["applied_total_fy"]["quantity"] == pytest.approx(4000.0) and short.data["loaded_nodes"] == ["b"]
    assert short.data["reaction_total_fy"]["quantity"] == pytest.approx(-4000.0)  # always minus the applied total: it cannot reveal a missing load
    assert "hand total" in short.data["load_check"]


def test_the_truss_load_totals_follow_the_unit_system_and_the_dimension():
    imperial = call2(truss2.truss, **ROOF, loads="b,0 N,4448.2216152605 N", unit_system="imperial")
    assert imperial.data["applied_total_fy"]["unit"] == "force_pound" and imperial.data["applied_total_fy"]["quantity"] == pytest.approx(1000.0)
    space = call2(
        truss2.truss, nodes="a,0 m,0 m,0 m;b,1 m,0 m,0 m;c,0 m,1 m,0 m;d,0 m,0 m,1 m", members="ab,a,b,200 GPa,100 mm^2;ac,a,c,200 GPa,100 mm^2;ad,a,d,200 GPa,100 mm^2;bc,b,c,200 GPa,100 mm^2;bd,b,d,200 GPa,100 mm^2;cd,c,d,200 GPa,100 mm^2",
        supports="a,xyz;b,yz;c,z", loads="d,1 kN,2 kN,3 kN", dimension=3,
    )
    assert space.ok, space.error
    assert [space.data[f"applied_total_f{a}"]["quantity"] for a in "xyz"] == pytest.approx([1000.0, 2000.0, 3000.0])
