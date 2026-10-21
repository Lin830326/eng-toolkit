"""Isotropic Hooke law, Mohr transformation, rosettes and strength criteria."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.domains.structures._common import Result, System, choice, invalid, number, q, run
from eng_toolkit.domains.structures._server import server


@server.tool(description="Mohr plane stress/strain transformation, 3D principal stresses and invariants, von Mises/Tresca and brittle Mohr-Coulomb/Rankine safety; 45/60-degree strain rosettes and isotropic Hooke law. Tension positive, engineering shear strain. Small strain, isotropic linear elasticity; no failure-code check.")
def stress(
    state: Annotated[str, Field(description="plane_stress, plane_strain, three_dimensional, rosette_45 or rosette_60; rosettes measure plane-stress surface strains.")] = "plane_stress",
    input_type: Annotated[str, Field(description="stress or strain; rosettes require strain.")] = "stress",
    sigma_x: Annotated[str, Field(description="x normal stress, tension positive, e.g. '80 MPa'; required for stress input outside rosettes; '' omits otherwise.")] = "",
    sigma_y: Annotated[str, Field(description="y normal stress, tension positive, e.g. '20 MPa'; required for stress input outside rosettes; '' omits otherwise.")] = "",
    sigma_z: Annotated[str, Field(description="z normal stress, e.g. '0 Pa'; required for three_dimensional stress input; '' omits for other modes.")] = "",
    tau_xy: Annotated[str, Field(description="xy shear stress, +y on +x face, e.g. '40 MPa'; required for stress input outside rosettes; '' omits otherwise.")] = "",
    tau_yz: Annotated[str, Field(description="yz shear stress, +z on +y face, e.g. '5 MPa'; required for three_dimensional stress input; '' omits otherwise.")] = "",
    tau_zx: Annotated[str, Field(description="zx shear stress, +x on +z face, e.g. '3 MPa'; required for three_dimensional stress input; '' omits otherwise.")] = "",
    epsilon_x: Annotated[str, Field(description="x normal strain, e.g. '0.001 dimensionless'; required for strain input outside rosettes; '' omits otherwise.")] = "",
    epsilon_y: Annotated[str, Field(description="y normal strain, e.g. '-0.0003 dimensionless'; required for strain input outside rosettes; '' omits otherwise.")] = "",
    epsilon_z: Annotated[str, Field(description="z normal strain, e.g. '0 dimensionless'; required for three_dimensional strain input; '' omits otherwise.")] = "",
    gamma_xy: Annotated[str, Field(description="xy engineering shear strain, twice epsilon_xy, e.g. '0.002 dimensionless'; required for non-rosette strain input; '' omits otherwise.")] = "",
    gamma_yz: Annotated[str, Field(description="yz engineering shear strain, twice epsilon_yz, e.g. '0.001 dimensionless'; required for 3D strain input; '' omits otherwise.")] = "",
    gamma_zx: Annotated[str, Field(description="zx engineering shear strain, twice epsilon_zx, e.g. '0.0005 dimensionless'; required for 3D strain input; '' omits otherwise.")] = "",
    strain_a: Annotated[str, Field(description="Rosette gauge a at 0 degrees, e.g. '0.001 dimensionless'; required for rosette_45/rosette_60 strain input; '' omits otherwise.")] = "",
    strain_b: Annotated[str, Field(description="Rosette gauge b at 45 or 60 degrees, e.g. '0.0008 dimensionless'; required for rosette strain input; '' omits otherwise.")] = "",
    strain_c: Annotated[str, Field(description="Rosette gauge c at 90 or 120 degrees, e.g. '0.0002 dimensionless'; required for rosette strain input; '' omits otherwise.")] = "",
    youngs_modulus: Annotated[str, Field(description="Isotropic Young's modulus for Hooke conversion, e.g. '200 GPa'; required for strain input; '' omits strains for stress input.")] = "",
    poisson_ratio: Annotated[float, Field(description="Isotropic transverse contraction ratio for Hooke law, e.g. 0.3; range -0.999..0.4999; used with youngs_modulus.")] = .3,
    angle: Annotated[str, Field(description="Counterclockwise rotation of in-plane coordinate axes for stress transformation, e.g. '30 deg'; default 0 deg.")] = "0 deg",
    yield_strength: Annotated[str, Field(description="Uniaxial yield stress for von Mises/Tresca safety factors, e.g. '250 MPa'; '' omits these factors.")] = "",
    tensile_strength: Annotated[str, Field(description="Positive tensile failure stress for brittle safety factors, e.g. '100 MPa'; requires compressive_strength; '' omits.")] = "",
    compressive_strength: Annotated[str, Field(description="Positive compressive failure stress for brittle safety factors, e.g. '300 MPa'; requires tensile_strength; '' omits.")] = "",
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(_):
        mode = choice(state, "state", ("plane_stress", "plane_strain", "three_dimensional", "rosette_45", "rosette_60"))
        kind = choice(input_type, "input_type", ("stress", "strain"))
        nu = number(poisson_ratio, "poisson_ratio", -.999, .4999)
        e = q(youngs_modulus, "youngs_modulus", "stress", optional=True)
        theta = q(angle, "angle", "angle", signed=True)
        sy = q(yield_strength, "yield_strength", "stress", optional=True)
        st = q(tensile_strength, "tensile_strength", "stress", optional=True)
        sc = q(compressive_strength, "compressive_strength", "stress", optional=True)
        if (st is None) != (sc is None):
            invalid("tensile_strength", "tensile and compressive strengths are required together")
        r = Result(unit_system, {
            "Mohr": "mean=(sx+sy)/2; R=hypot((sx-sy)/2,txy); theta_p=atan2(2*txy,sx-sy)/2; sx'=mean+d*cos(2t)+txy*sin(2t)",
            "principal": "eigvalsh(sigma-mean(sigma)*identity)+mean(sigma); characteristic polynomial s^3-I1*s^2+I2*s-I3=0",
            "Hooke": "epsilon=(1+nu)*sigma/E-nu*trace(sigma)*identity/E; sigma=2G*epsilon+lambda*trace(epsilon)*identity",
            "rosette": "45: ex=a,ey=c,gxy=2b-a-c; 60: ex=a,ey=(2b+2c-a)/3,gxy=2(b-c)/sqrt(3)",
            "yield": "von Mises=sqrt(((sx-sy)^2+(sy-sz)^2+(sz-sx)^2)/2+3*(txy^2+tyz^2+tzx^2)); Tresca=max(dev)-min(dev)",
            "brittle": "Rankine utilization=max(s1/St,-s3/Sc,0); Mohr-Coulomb=s1/St-s3/Sc for mixed signs, otherwise the active uniaxial ratio",
        }, "Tension positive; tau_xy is positive on +x face in +y. Angle rotates coordinate axes counterclockwise; gamma=2*epsilon_xy. Isotropic small-strain Hooke law. Rosette a,b,c angles are 0,45,90 or 0,60,120 degrees.",
                   "-0.999<=nu<=0.4999; positive E and strengths. Plane stress has sigma_z=0; plane strain has epsilon_z=0 and sigma_z=nu*(sigma_x+sigma_y). No plastic flow or fatigue model.")
        stress_raw = [sigma_x, sigma_y, sigma_z, tau_xy, tau_yz, tau_zx]
        strain_raw = [epsilon_x, epsilon_y, epsilon_z, gamma_xy, gamma_yz, gamma_zx]
        rosette_raw = [strain_a, strain_b, strain_c]
        stress_names = ["sigma_x", "sigma_y", "sigma_z", "tau_xy", "tau_yz", "tau_zx"]
        strain_names = ["epsilon_x", "epsilon_y", "epsilon_z", "gamma_xy", "gamma_yz", "gamma_zx"]
        ros = mode.startswith("rosette")
        if ros:
            if kind != "strain" or any(stress_raw+strain_raw):
                invalid("state", "rosettes accept only strain_a, strain_b, strain_c with input_type=strain")
            av, bv, cv = [q(v, n, "strain", signed=True) for v, n in zip(rosette_raw, ("strain_a","strain_b","strain_c"))]
            ex, ey, gxy = (av, cv, 2*bv-av-cv) if mode == "rosette_45" else (av, (2*bv+2*cv-av)/3, 2*(bv-cv)/math.sqrt(3))
            eps = np.array([[ex, gxy/2, 0], [gxy/2, ey, 0], [0, 0, 0.]])
        else:
            if any(rosette_raw):
                invalid("strain_a", "rosette values require a rosette state")
            raw, names = (stress_raw, stress_names) if kind == "stress" else (strain_raw, strain_names)
            if any(strain_raw if kind == "stress" else stress_raw):
                invalid("input_type", "stress and strain component inputs cannot be mixed")
            if mode != "three_dimensional" and any(raw[j] for j in (2,4,5)):
                invalid(names[2], "out-of-plane components are derived for plane states")
            indices = range(6) if mode == "three_dimensional" else (0,1,3)
            values = [0.]*6
            for j in indices:
                values[j] = q(raw[j], names[j], "stress" if kind == "stress" else "strain", signed=True)
            x, y, z, xy, yz, zx = values
            divisor = 1 if kind == "stress" else 2
            tensor = np.array([[x,xy/divisor,zx/divisor],[xy/divisor,y,yz/divisor],[zx/divisor,yz/divisor,z]])
            if kind == "stress":
                sigma = tensor
                if mode == "plane_strain":
                    sigma[2,2] = nu*(x+y)
            else:
                eps = tensor
        if kind == "strain":
            if e is None:
                invalid("youngs_modulus", "strain conversion requires Young's modulus")
            if mode == "plane_stress" or ros:
                eps[2,2] = -nu/(1-nu)*(eps[0,0]+eps[1,1])
            g = e/(2*(1+nu))
            lam = e*nu/((1+nu)*(1-2*nu))
            sigma = 2*g*eps+lam*np.trace(eps)*np.eye(3)
        if e is not None:
            eps = ((1+nu)*sigma-nu*np.trace(sigma)*np.eye(3))/e
            for name, (a,b) in zip(strain_names, ((0,0),(1,1),(2,2),(0,1),(1,2),(2,0))):
                r.add(name, float(eps[a,b])*(1 if a == b else 2))
            strain_principal = np.linalg.eigvalsh(eps if mode == "three_dimensional" else eps[:2,:2])[::-1]
            for j,val in enumerate(strain_principal,1):
                r.add(f"principal_strain_{j}",val)
            if mode != "three_dimensional":
                r.add("principal_strain_angle", .5*math.atan2(2*eps[0,1], eps[0,0]-eps[1,1]), "angle")
            if np.max(np.abs(eps)) > .01:
                r.warnings.append("Strain exceeds 1%; the small-strain linear model needs review.")
        mean_stress = math.fsum(np.diag(sigma))/3
        deviators = np.linalg.eigvalsh(sigma-mean_stress*np.eye(3))[::-1]
        principals = deviators+mean_stress
        for j, val in enumerate(principals):
            r.add(f"principal_stress_{j+1}", val, "stress")
        for name, (a,b) in zip(stress_names, ((0,0),(1,1),(2,2),(0,1),(1,2),(2,0))):
            r.add(name, sigma[a,b], "stress")
        s1, s2, s3 = principals
        sx,syy,sz = np.diag(sigma)
        vm = math.sqrt(((sx-syy)**2+(syy-sz)**2+(sz-sx)**2)/2+3*(sigma[0,1]**2+sigma[1,2]**2+sigma[2,0]**2))
        tresca = deviators[0]-deviators[2]
        r.add("von_mises", vm, "stress")
        r.add("tresca", tresca, "stress")
        r.add("maximum_shear_3d", tresca/2, "stress")
        r.add("I1", np.trace(sigma), "stress")
        # Pair products avoid cancellation for nearly hydrostatic states.
        r.add("I2", s1*s2+s2*s3+s3*s1, "stress2")
        r.add("I3", s1*s2*s3, "stress3")
        sx, syy, t = sigma[0,0], sigma[1,1], sigma[0,1]
        mean, diff = (sx+syy)/2, (sx-syy)/2
        radius = math.hypot(diff,t)
        r.add("in_plane_principal_1", mean+radius, "stress")
        r.add("in_plane_principal_2", mean-radius, "stress")
        r.add("principal_angle", .5*math.atan2(2*t,sx-syy), "angle")
        r.data["principal_angle_unique"] = radius > 1e-12*max(abs(sx),abs(syy),abs(t),1)
        r.add("maximum_shear_in_plane", radius, "stress")
        r.add("transformed_sigma_x", mean+diff*math.cos(2*theta)+t*math.sin(2*theta), "stress")
        r.add("transformed_sigma_y", mean-diff*math.cos(2*theta)-t*math.sin(2*theta), "stress")
        r.add("transformed_tau_xy", -diff*math.sin(2*theta)+t*math.cos(2*theta), "stress")
        r.safety("von_mises_safety_factor", sy, vm)
        r.safety("tresca_safety_factor", sy, tresca)
        if st is not None:
            rankine = max(s1/st,-s3/sc,0)
            coulomb = max(s1,0)/st+max(-s3,0)/sc
            r.safety("maximum_principal_safety_factor", 1., rankine)
            r.safety("mohr_coulomb_safety_factor", 1., coulomb)
        return r.done("von_mises")
    return run("stress", inputs, compute)
