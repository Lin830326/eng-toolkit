"""Transformer test reduction and linear T-equivalent phasor calculations."""

from __future__ import annotations

import math
from typing import Annotated

from pydantic import Field

from eng_toolkit.domains.electric._common import Result, choice, fields, invalid, number, q, records, run, scalar
from eng_toolkit.domains.electric._server import server


@server.tool(description="Single/three-phase transformer OC/SC tests or T circuit: referred impedances, exact linear phasor and approximate regulation, losses, maximum/all-day efficiency, per-unit and additive autotransformer ratings. Chapman/Fitzgerald methods; sinusoidal, unsaturated steady state.")
def transformer(
    rating: Annotated[str, Field(description="Total nameplate apparent power, e.g. '20 kVA'; required in all modes, VA family only.")] = "",
    primary_voltage: Annotated[str, Field(description="Rated primary line voltage, e.g. '8000 V'; single-phase terminal voltage for single connection.")] = "",
    secondary_voltage: Annotated[str, Field(description="Rated secondary line voltage, e.g. '240 V'; single-phase terminal voltage for single connection.")] = "",
    connection: Annotated[str, Field(description="Winding connection: single, Y-Y, Y-Delta, Delta-Y or Delta-Delta; positive abc sequence.")] = "single",
    mode: Annotated[str, Field(description="Parameter source: tests for OC/SC estimates, circuit for a complete linear T equivalent.")] = "tests",
    open_circuit: Annotated[str, Field(description="Tests only: 'line V,line I,total real P', e.g. '8000 V,0.214 A,400 W'; '' omitted in circuit mode.")] = "",
    short_circuit: Annotated[str, Field(description="Tests only: 'line V,line I,total real P', e.g. '489 V,2.5 A,240 W'; '' omitted in circuit mode.")] = "",
    oc_side: Annotated[str, Field(description="Side of open-circuit measurements: primary or secondary; used in tests mode.")] = "primary",
    sc_side: Annotated[str, Field(description="Side of short-circuit measurements: primary or secondary; used in tests mode.")] = "primary",
    circuit: Annotated[str, Field(description="Circuit mode: 'Rc,Xm,R1,X1,R2,X2', e.g. '144 ohm,35 ohm,.02 ohm,.08 ohm,.02 ohm,.08 ohm'; all on circuit_side.")] = "",
    circuit_side: Annotated[str, Field(description="Reference side for all six T-circuit impedances: primary or secondary; circuit mode only.")] = "secondary",
    primary_fraction: Annotated[float, Field(description="Tests only: assumed fraction of total R and X in primary winding, 0..1; tests cannot identify the split.")] = .5,
    referred_side: Annotated[str, Field(description="Reference side for reported Rc, Xm, Req, Xeq: primary or secondary.")] = "secondary",
    load_fraction: Annotated[float, Field(description="Secondary winding current divided by rated winding current, 0..2; overload is an estimate only.")] = 1.,
    power_factor: Annotated[float, Field(description="Load displacement power factor magnitude, 0.001..1; sign chosen by power_factor_direction.")] = .8,
    power_factor_direction: Annotated[str, Field(description="Load reactive-current direction: lagging or leading; positive real output in both cases.")] = "lagging",
    load_profile: Annotated[str, Field(description="All-day 'duration,fraction,pf' rows, e.g. '8 h,1,.8;16 h,0,1'; <=48 rows totaling 24 h; '' omits.")] = "",
    autotransformer: Annotated[bool, Field(description="If true, report additive autotransformer ratings with primary common winding and secondary series winding.")] = False,
):
    inputs = locals()

    def compute(handle):
        r = Result({
            "test_reduction": "Chapman OC: Rc=Vph²/(P/n), Xm=Vph/sqrt(Iph²-(P/(n Vph))²); SC: Req=P/(n Iph²), Xeq=sqrt((Vph/Iph)²-Req²)",
            "referred_impedance": "Fitzgerald-Kingsley-Umans: Z_primary=a² Z_secondary, a=V1ph/V2ph",
            "exact_T": "E=V2+I2 Z2; I0=E(1/Rc-j/Xm); I1'=I2+I0; V1'=E+I1' Z1; Vnl=V1'/(1+Z1 Ym)",
            "efficiency": "eta=Pout/(Pout+Pcore+x²Pcu_fl); xmax=sqrt(Pcore/Pcu_fl)",
        }, "RMS winding phasors; fixed rated secondary terminal voltage and specified current. Exact means exact algebra for the supplied linear T model. OC/SC extraction neglects no-load series drop and SC excitation. Constant parameters and core loss for approximate efficiency.",
        "Sinusoidal 50/60-Hz-style equivalent circuit at the test frequency; no saturation, inrush, harmonics or temperature correction. Current fraction 0..2. Three-phase balanced positive sequence, ideal vector-group polarity defined in data.")
        s = q(rating, "rating", "VA", power="apparent", hi=1e12)
        v1 = q(primary_voltage, "primary_voltage", "V", hi=1e7)
        v2 = q(secondary_voltage, "secondary_voltage", "V", hi=1e7)
        con = choice(connection, "connection", ("single", "Y-Y", "Y-Delta", "Delta-Y", "Delta-Delta"))
        source = choice(mode, "mode", ("tests", "circuit"))
        side = choice(referred_side, "referred_side", ("primary", "secondary"))
        oc = choice(oc_side, "oc_side", ("primary", "secondary"))
        sc = choice(sc_side, "sc_side", ("primary", "secondary"))
        cs = choice(circuit_side, "circuit_side", ("primary", "secondary"))
        x = number(load_fraction, "load_fraction", 0, 2)
        pf = number(power_factor, "power_factor", .001, 1)
        lag = choice(power_factor_direction, "power_factor_direction", ("lagging", "leading")) == "lagging"
        fraction = number(primary_fraction, "primary_fraction", 0, 1)
        n = 1 if con == "single" else 3
        c1, c2 = ("single", "single") if n == 1 else con.split("-")
        vf1, vf2 = (math.sqrt(3) if c == "Y" else 1 for c in (c1, c2))
        vp1, vp2 = v1/vf1, v2/vf2
        a = vp1/vp2
        if not 1e-6 <= a <= 1e6:
            invalid("primary_voltage", "winding voltage ratio must be 1e-6..1e6", "input.out_of_range")
        iph = s/(n*vp2)

        def test_row(raw, name, measured_side):
            rows = records(raw, name, 1, required=True)
            fields(rows[0], (3,), name + "/0")
            volts = q(rows[0][0], name+"/0/0", "V", hi=1e7)
            amps = q(rows[0][1], name+"/0/1", "A", hi=1e9)
            watts = q(rows[0][2], name+"/0/2", "W", power="real", hi=1e12)
            winding = c1 if measured_side == "primary" else c2
            volts /= math.sqrt(3) if winding == "Y" else 1
            amps /= math.sqrt(3) if winding == "Delta" else 1
            if watts >= n*volts*amps:
                invalid(name+"/0/2", "test power must be below V*I times phase count for a positive reactive branch")
            return volts, amps, watts/n

        if source == "tests":
            if circuit:
                invalid("circuit", "circuit conflicts with tests mode")
            vo, io, po = test_row(open_circuit, "open_circuit", oc)
            vs, isc, psc = test_row(short_circuit, "short_circuit", sc)
            # Product form avoids cancellation of squared close numbers.
            ic = po/vo
            im = math.sqrt((io-ic)*(io+ic))
            rc, xm = vo*vo/po, vo/im
            req, zeq = psc/(isc*isc), vs/isc
            xeq = math.sqrt((zeq-req)*(zeq+req))
            if oc == "primary":
                rc, xm = rc/(a*a), xm/(a*a)
            if sc == "primary":
                req, xeq = req/(a*a), xeq/(a*a)
            r1, x1, r2, x2 = fraction*req, fraction*xeq, (1-fraction)*req, (1-fraction)*xeq
            r.warnings.append("OC/SC tests identify approximate shunt and total series parameters, not the individual winding split. Exact T results use the stated primary_fraction assumption.")
            r.data["exact_circuit_identification"] = "assumed winding split from OC/SC estimates"
        else:
            if open_circuit or short_circuit or primary_fraction != .5:
                invalid("open_circuit", "test data or primary_fraction conflict with circuit mode")
            rows = records(circuit, "circuit", 1, required=True)
            fields(rows[0], (6,), "circuit/0")
            rc, xm, r1, x1, r2, x2 = [q(raw, f"circuit/0/{i}", "ohm", zero=i >= 2, hi=1e12) for i, raw in enumerate(rows[0])]
            if cs == "primary":
                rc, xm, r1, x1, r2, x2 = [z/(a*a) for z in (rc, xm, r1, x1, r2, x2)]
            req, xeq = r1+r2, x1+x2
            r.data["exact_circuit_identification"] = "caller supplied T equivalent"
        if req == 0:
            invalid("circuit", "positive winding resistance is required for a finite maximum-efficiency load")
        ym = complex(1/rc, -1/xm)
        z1, z2 = complex(r1, x1), complex(r2, x2)
        sinphi = math.sqrt((1-pf)*(1+pf)) * (1 if lag else -1)
        i2 = x*iph*complex(pf, -sinphi)
        e = vp2+i2*z2
        i0 = e*ym
        i1 = i2+i0
        supply = e+i1*z1
        no_load = supply/(1+z1*ym)
        # Stable magnitude difference relative to a positive real reference.
        delta = no_load-vp2
        reg_exact = (2*vp2*delta.real+abs(delta)**2)/((abs(no_load)+vp2)*vp2)
        reg_approx = x*iph*(req*pf+xeq*sinphi)/vp2
        core = n*vp2*vp2/rc
        copper_fl = n*iph*iph*req
        output = x*s*pf
        approx_losses = core+x*x*copper_fl
        exact_core = n*abs(e)**2/rc
        exact_copper = n*(abs(i1)**2*r1+abs(i2)**2*r2)
        # Real parts of the complex source-power product can cancel when the
        # reactive voltage is enormous. The passive-circuit energy identity is
        # algebraically identical and sums only nonnegative real terms.
        pin = math.fsum((output, exact_core, exact_copper))
        xmax = math.sqrt(core/copper_fl)
        eta_max = xmax*s*pf/(xmax*s*pf+2*core)
        # The stated validity range is current fraction 0..2; a stationary
        # maximum beyond it is not a model result and leaves `values`.
        x_limit = 2.0
        inside = xmax <= x_limit
        maximum = (("maximum_efficiency_load_fraction", xmax, "dimensionless"), ("maximum_efficiency", eta_max, "dimensionless")) if inside else ()
        for name, value, unit in maximum + (
            ("turns_ratio", a, "dimensionless"), ("line_voltage_ratio", v1/v2, "dimensionless"),
            ("primary_phase_voltage", vp1, "V"), ("secondary_phase_voltage", vp2, "V"),
            ("primary_line_current_rated", s/v1/(math.sqrt(3) if n == 3 else 1), "A"),
            ("secondary_line_current_rated", s/v2/(math.sqrt(3) if n == 3 else 1), "A"),
            ("secondary_phase_current_rated", iph, "A"), ("regulation_approximate", reg_approx, "dimensionless"),
            ("regulation_exact", reg_exact, "dimensionless"), ("efficiency_approximate", output/(output+approx_losses), "dimensionless"),
            ("efficiency_exact", output/pin, "dimensionless"), ("output_power", output, "W"), ("input_power_exact", pin, "W"),
            ("core_loss_exact", exact_core, "W"), ("copper_loss_exact", exact_copper, "W"),
            ("core_loss_approximate", core, "W"), ("full_load_copper_loss", copper_fl, "W"),
            ("required_primary_line_voltage", abs(supply)*a*vf1, "V"),
        ):
            r.add(name, value, unit)
        r.data["maximum_efficiency_outside_validity_range"] = not inside
        if not inside:
            # Efficiency rises monotonically up to xmax, so the range-limited
            # maximum lies at the validity limit x=2 for the stated pf.
            eta_limit = x_limit*s*pf/(x_limit*s*pf+core+x_limit*x_limit*copper_fl)
            r.add("efficiency_at_load_fraction_limit", eta_limit)
            r.data["unconstrained_maximum_efficiency_load_fraction"] = xmax
            r.data["unconstrained_maximum_efficiency"] = eta_max
            r.data["load_fraction_validity_limit"] = x_limit
            r.warnings.append(f"Unconstrained maximum-efficiency load fraction {xmax:.4g} exceeds the 0..2 validity range; "
                              "maximum_efficiency values are omitted (see data unconstrained_*), and "
                              "efficiency_at_load_fraction_limit is the range-limited maximum at x=2.")
        factor = a*a if side == "primary" else 1
        for name, z in (("core_resistance", rc), ("magnetizing_reactance", xm), ("equivalent_resistance", req), ("equivalent_reactance", xeq)):
            r.add(name, z*factor, "ohm")
        base_z = vp2/iph
        r.data["equivalent_circuits"] = {
            ref: {"unit": "ohm", **{k: value*scale for k, value in zip(("Rc", "Xm", "R1", "X1", "R2", "X2"), (rc, xm, r1, x1, r2, x2))}}
            for ref, scale in (("secondary", 1), ("primary", a*a))
        }
        r.data["per_unit"] = {"R_eq": req/base_z, "X_eq": xeq/base_z, "R_c": rc/base_z, "X_m": xm/base_z,
                              "base_apparent_power_VA": s, "primary_base_impedance_ohm": base_z*a*a, "secondary_base_impedance_ohm": base_z}
        shifts = {"single": 0, "Y-Y": 0, "Delta-Delta": 0, "Y-Delta": -30, "Delta-Y": 30}
        r.add("secondary_line_phase_shift", shifts[con], "deg")
        r.data["connection_convention"] = "Same-polarity winding phase EMFs; secondary line voltage relative to primary line voltage. Y-Delta=-30 deg, Delta-Y=+30 deg. Other clock groups require winding reconnection, not inferred here."
        if x > 1 or xmax > 1:
            r.warnings.append("Overload or unconstrained maximum-efficiency current exceeds nameplate; thermal capability is not evaluated.")
        if load_profile:
            hours, energy, losses = 0., 0., 0.
            output_rows = []
            for i, row in enumerate(records(load_profile, "load_profile", 48, required=True)):
                path = f"load_profile/{i}"
                fields(row, (3,), path)
                duration = q(row[0], path+"/0", "s", hi=86400)
                load = scalar(row[1], path+"/1", 0, 2)
                cos = scalar(row[2], path+"/2", .001, 1)
                eout = duration*load*s*cos
                eloss = duration*(core+load*load*copper_fl)
                hours += duration
                energy += eout
                losses += eloss
                output_rows.append([duration, load, cos, eout, eloss])
            if abs(hours-86400) > 1e-6:
                invalid("load_profile", "all-day profile durations must total 24 h")
            r.add("all_day_efficiency", energy/(energy+losses))
            r.add("all_day_output_energy", energy, "J")
            r.add("all_day_loss_energy", losses, "J")
            r.csv(handle, "load_profile.csv", ["duration_s", "load_fraction", "power_factor", "output_J", "loss_J"], output_rows)
        if autotransformer:
            if con not in ("single", "Y-Y", "Delta-Delta"):
                invalid("autotransformer", "additive autotransformer requires matching winding connections")
            # Series-winding ampacity limits the additive connection if V1 >= V2.
            if vp1 < vp2:
                invalid("primary_voltage", "autotransformer model requires primary common voltage >= secondary series voltage")
            auto_rating = s*(vp1+vp2)/vp2
            r.add("autotransformer_rating", auto_rating, "VA")
            r.add("autotransformer_transformed_power", s, "VA")
            r.add("autotransformer_conducted_power", auto_rating-s, "VA")
            r.add("autotransformer_high_line_voltage", v1+v2, "V")
            r.warnings.append("Additive autotransformer connection has no galvanic isolation; insulation and fault duty are outside this model.")
        return r.done("efficiency_exact")

    return run("transformer", inputs, compute)
