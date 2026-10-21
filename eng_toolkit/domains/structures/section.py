"""Exact area moments of polygonal sections and standard shapes."""

import math
from typing import Annotated

import numpy as np
from pydantic import Field

from eng_toolkit.domains.structures._common import Result, System, choice, fields, invalid, q, records, run
from eng_toolkit.domains.structures._server import server


def integrals(vertices):
    """Signed A, first moments x/y, Iy, Ix, Ixy about the supplied origin."""
    p = np.asarray(vertices, dtype=float)
    x, y = p.T
    xx, yy = np.roll(p, -1, axis=0).T
    cross = x*yy-xx*y
    return np.array([
        math.fsum(cross)/2,
        math.fsum((x+xx)*cross)/6,
        math.fsum((y+yy)*cross)/6,
        math.fsum((x*x+x*xx+xx*xx)*cross)/12,
        math.fsum((y*y+y*yy+yy*yy)*cross)/12,
        math.fsum((2*x*y+x*yy+xx*y+2*xx*yy)*cross)/24,
    ])


def cross(a, b, c):
    return float((b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0]))


def on_segment(a, b, p, tol):
    return abs(cross(a,b,p)) <= tol and all(min(a[j],b[j])-tol <= p[j] <= max(a[j],b[j])+tol for j in (0,1))


def intersects(a, b, c, d, tol=1e-12):
    ab_c, ab_d, cd_a, cd_b = cross(a,b,c),cross(a,b,d),cross(c,d,a),cross(c,d,b)
    if ((ab_c > tol and ab_d < -tol) or (ab_c < -tol and ab_d > tol)) and ((cd_a > tol and cd_b < -tol) or (cd_a < -tol and cd_b > tol)):
        return True
    return any((on_segment(a,b,c,tol),on_segment(a,b,d,tol),on_segment(c,d,a,tol),on_segment(c,d,b,tol)))


def inside(point, ring):
    result = False
    x, y = point
    for a,b in zip(ring, np.roll(ring,-1,axis=0)):
        if (a[1] > y) != (b[1] > y) and x < (b[0]-a[0])*(y-a[1])/(b[1]-a[1])+a[0]:
            result = not result
    return result


def parse_polygon(polygon, holes):
    if len(holes) > 60000:
        invalid("holes", "text cap is 60000 characters", "input.out_of_range")
    texts = [polygon] + (holes.split("|") if holes else [])
    if len(texts) > 17:
        invalid("holes", "at most 16 holes", "input.out_of_range")
    rings = []
    for j,text in enumerate(texts):
        path = "polygon" if j == 0 else f"holes/{j-1}"
        rows = records(text,path,200,required=True)
        vertices = []
        for k,row in enumerate(rows):
            fields(row,(2,),f"{path}/{k}")
            vertices.append([q(v,f"{path}/{k}/{axis}","length",signed=True) for axis,v in enumerate(row)])
        p = np.array(vertices)
        if len(p) > 1 and np.array_equal(p[0],p[-1]):
            p = p[:-1]
        if len(p) < 3:
            invalid(path,"a polygon needs at least three vertices")
        rings.append(p)
    if sum(map(len,rings)) > 200:
        invalid("polygon","total vertex cap including holes is 200","input.out_of_range")
    origin = rings[0][0].copy()
    scale = np.ptp(rings[0],axis=0).max()
    if scale < 1e-9:
        invalid("polygon","zero or unresolved polygon span")
    normalized = [(p-origin)/scale for p in rings]
    for j,p in enumerate(normalized):
        path = "polygon" if j == 0 else f"holes/{j-1}"
        n = len(p)
        for k in range(n):
            if np.linalg.norm(p[(k+1)%n]-p[k]) < 1e-12:
                invalid(path,"duplicate or unresolved adjacent vertices")
            # Collinear reversal overlaps even though adjacent edges share a vertex.
            a,b,c = p[k-1],p[k],p[(k+1)%n]
            if abs(cross(a,b,c)) <= 1e-12 and np.dot(a-b,c-b) > 0:
                invalid(path,"polygon has overlapping adjacent edges")
            for m in range(k+1,n):
                if m == k+1 or (k == 0 and m == n-1):
                    continue
                if intersects(p[k],p[(k+1)%n],p[m],p[(m+1)%n]):
                    invalid(path,"self-intersecting or touching polygon")
        if abs(integrals(p)[0]) < 1e-12:
            invalid(path,"zero or unresolved polygon area")
        for previous in normalized[:j]:
            if any(intersects(a,b,c,d) for a,b in zip(p,np.roll(p,-1,axis=0)) for c,d in zip(previous,np.roll(previous,-1,axis=0))):
                invalid(path,"rings must not intersect or touch")
        if j and (not inside(p[0],normalized[0]) or any(inside(p[0],h) or inside(h[0],p) for h in normalized[1:j])):
            invalid(path,"holes must lie strictly inside the outer ring and cannot overlap or nest")
    return [(p if integrals((p-origin)/scale)[0] > 0 else p[::-1], 1 if j == 0 else -1) for j,p in enumerate(rings)]


def rectangle_ring(x0, y0, width, height):
    return np.array([[x0,y0],[x0+width,y0],[x0+width,y0+height],[x0,y0+height]])


def polygon_properties(rings):
    origin = rings[0][0][0]
    first = sum((sign*integrals(p-origin) for p,sign in rings), np.zeros(6))
    area = first[0]
    if area <= 1e-14:
        invalid("polygon","net area is nonpositive or below 1e-14 m^2")
    centroid_local = first[1:3]/area
    # Integrate again around the centroid, never subtract large origin moments.
    central = sum((sign*integrals((p-origin)-centroid_local) for p,sign in rings), np.zeros(6))
    return area, origin+centroid_local, central[4],central[3],central[5]


def clip_above(p, height, axis=1):
    out = []
    for a,b in zip(p,np.roll(p,-1,axis=0)):
        ain, bin = a[axis] >= height, b[axis] >= height
        if ain:
            out.append(a)
        if ain != bin:
            out.append(a+(b-a)*(height-a[axis])/(b[axis]-a[axis]))
    return np.asarray(out)


def first_moment(rings, centroid, height, axis=1):
    total = 0.
    for p,sign in rings:
        clipped = clip_above(p-centroid, height-centroid[axis], axis)
        if len(clipped) >= 3:
            total += sign*integrals(clipped)[axis+1]
    return total


@server.tool(description="Plane-section area, centroid, Green-theorem centroidal/principal moments, radii and elastic moduli; standard-shape Saint-Venant/Bredt torsion, symmetric plastic moduli, Q and VQ/I. Polygon holes accepted with either winding, max 200 vertices/16 holes. Thin-wall torsion approximations are flagged; no warping analysis.")
def section(
    shape: Annotated[str, Field(description="rectangle, circle, hollow_circle, hollow_rectangle, i, h, t, channel, or polygon. Dimensions are full outer dimensions.")] = "rectangle",
    width: Annotated[str, Field(description="Overall section/flange width, e.g. '150 mm'; required for rectangle/hollow_rectangle/i/h/t/channel; '' omits otherwise.")] = "",
    height: Annotated[str, Field(description="Overall bottom-to-top height, e.g. '300 mm'; required for rectangle/hollow_rectangle/i/h/t/channel; '' omits otherwise.")] = "",
    diameter: Annotated[str, Field(description="Outer diameter, e.g. '100 mm'; required for circle/hollow_circle; '' omits for other shapes.")] = "",
    thickness: Annotated[str, Field(description="Uniform wall thickness, e.g. '5 mm'; required for hollow_circle/hollow_rectangle; '' omits otherwise.")] = "",
    web_thickness: Annotated[str, Field(description="Vertical web thickness, e.g. '6 mm'; required for i/h/t/channel; '' omits otherwise.")] = "",
    flange_thickness: Annotated[str, Field(description="Horizontal flange thickness, e.g. '8 mm'; required for i/h/t/channel; '' omits otherwise.")] = "",
    polygon: Annotated[str, Field(description="Outer ring 'x,y' length pairs, e.g. '0 m,0 m;1 m,0 m;0 m,1 m'; required for polygon; '' omits for other shapes.")] = "",
    holes: Annotated[str, Field(description="Polygon void rings of x,y lengths, e.g. '1 mm,1 mm;2 mm,1 mm;1 mm,2 mm'; separate rings with '|'; '' omits holes.")] = "",
    at_height: Annotated[str, Field(description="Absolute y coordinate of the horizontal Q cut, e.g. '75 mm'; '' omits; required with shear_force.")] = "",
    shear_force: Annotated[str, Field(description="Signed transverse force for shear flow VQ/Ix, e.g. '10 kN'; requires at_height; '' omits shear flow.")] = "",
    unit_system: System = "metric",
):
    inputs = dict(locals())

    def compute(_):
        kind = choice(shape,"shape",("rectangle","circle","hollow_circle","hollow_rectangle","i","h","t","channel","polygon"))
        r = Result(unit_system, {
            "Green": "A=sum(cross)/2; centroid=sum((p+q)*cross)/(6A); Ix=sum((y^2+yY+Y^2)*cross)/12, translated to centroid before integration",
            "principal": "I1,2=eigvalsh([[Ix,-Ixy],[-Ixy,Iy]]), scaled before solving; theta1=atan2(-2Ixy,Ix-Iy)/2; rx=sqrt(Ix/A)",
            "elastic_plastic": "Sx=Ix/c_y, Sy=Iy/c_x; Zx=integral(abs(y-y_pna))dA, symmetric y_pna=centroid; shape factor=Z/S",
            "torsion": "circle J=pi*(D^4-d^4)/32; rectangle J=b*t^3/3*[1-192*t/(pi^5*b)*sum_odd(tanh(n*pi*b/(2*t))/n^5)] (101 terms); closed thin tube J=4*Am^2/sum(s/t); open thin walls J=sum(b*t^3)/3",
            "shear_flow": "Q=integral_above_height(y-yc)dA; q=V*Q/Ix",
        }, "Uniform material; x horizontal and y upward. Standard rectangles/open shapes have bottom y=0, x centred on outer width; circles are centred at (0,0). Ix=integral(y-yc)^2 dA; Ixy=integral(x-xc)(y-yc)dA. Elastic fibres use centroidal axes.",
                   "Polygon geometry is exact in double precision; nonintersecting holes strictly inside outer boundary. General polygon Saint-Venant torsion and plastic moduli are not inferred. Thin-wall formulas need wall thickness <=10% of relevant span.")
        dims = {n:q(raw,n,"length",optional=True) for n,raw in (("width",width),("height",height),("diameter",diameter),("thickness",thickness),("web_thickness",web_thickness),("flange_thickness",flange_thickness))}
        expected = {"rectangle":("width","height"),"circle":("diameter",),"hollow_circle":("diameter","thickness"),"hollow_rectangle":("width","height","thickness"),"i":("width","height","web_thickness","flange_thickness"),"h":("width","height","web_thickness","flange_thickness"),"t":("width","height","web_thickness","flange_thickness"),"channel":("width","height","web_thickness","flange_thickness"),"polygon":()}[kind]
        for name,val in dims.items():
            if name in expected and val is None:
                invalid(name,"required shape dimension is missing","input.missing")
            if name not in expected and val is not None:
                invalid(name,"dimension does not apply to this shape")
        if kind != "polygon" and (polygon or holes):
            invalid("polygon","polygon text conflicts with standard shape dimensions")
        b,h,d,t,tw,tf = (dims[n] for n in ("width","height","diameter","thickness","web_thickness","flange_thickness"))
        circles = kind in ("circle","hollow_circle")
        rings = []
        torsion = None
        plastic = None
        if circles:
            radius = d/2
            inner = 0. if kind == "circle" else radius-t
            if inner < 0 or (kind == "hollow_circle" and inner <= 0):
                invalid("thickness","wall thickness must be less than radius")
            a = math.pi*(radius-inner)*(radius+inner)
            ix = iy = math.pi*(radius-inner)*(radius+inner)*(radius**2+inner**2)/4
            ixy = 0.
            centroid = np.zeros(2)
            xmin,ymin,xmax,ymax = -radius,-radius,radius,radius
            torsion = 2*ix
            plastic = (4*(radius**3-inner**3)/3,)*2
        else:
            if kind == "polygon":
                rings = parse_polygon(polygon,holes)
            elif kind in ("rectangle","hollow_rectangle"):
                rings = [(rectangle_ring(-b/2,0,b,h),1)]
                if kind == "hollow_rectangle":
                    if 2*t >= min(b,h):
                        invalid("thickness","twice wall thickness must be below both outer dimensions")
                    rings.append((rectangle_ring(-b/2+t,t,b-2*t,h-2*t),-1))
                    torsion = 4*((b-t)*(h-t))**2/(2*(b+h-2*t)/t)
                    if t/min(b-t,h-t) > .1:
                        r.warnings.append("Bredt thin-wall torsion is outside t/span<=0.1; use a section torsion solution for thick walls.")
                    r.data["torsion_method"] = "Bredt single closed cell, median-line dimensions"
                else:
                    wide,thin = max(b,h),min(b,h)
                    series = math.fsum(math.tanh(n*math.pi*wide/(2*thin))/n**5 for n in range(1,202,2))
                    torsion = wide*thin**3/3*(1-192*thin/(math.pi**5*wide)*series)
                    r.data["torsion_method"] = "Saint-Venant rectangle Fourier series, 101 odd terms"
            else:
                if tw >= b or (2*tf if kind != "t" else tf) >= h:
                    invalid("web_thickness","web must be narrower than width and flanges must leave positive web height")
                web_height = h-(tf if kind == "t" else 2*tf)
                xweb = -b/2 if kind == "channel" else -tw/2
                rings = [(rectangle_ring(-b/2,h-tf,b,tf),1), (rectangle_ring(xweb,0 if kind == "t" else tf,tw,web_height),1)]
                if kind != "t":
                    rings.append((rectangle_ring(-b/2,0,b,tf),1))
                r.data["torsion_method"] = "Thin open walls, sum(b*t^3)/3, junction corrections omitted"
                flange_span = b-tw if kind == "channel" else (b-tw)/2
                if max(tf/flange_span,tw/web_height) <= .25:
                    torsion = ((1 if kind == "t" else 2)*b*tf**3+web_height*tw**3)/3
                else:
                    r.warnings.append("Torsion constant omitted: the thin-wall formula is not valid when web thickness/clear web height or flange thickness/clear outstand exceeds 0.25 (the sum b t^3/3 then overstates J by roughly 15 % or more).")
            a,centroid,ix,iy,ixy = polygon_properties(rings)
            outer = np.concatenate([p for p,sign in rings if sign > 0])
            xmin,ymin = np.min(outer,axis=0)
            xmax,ymax = np.max(outer,axis=0)
            if kind in ("rectangle","hollow_rectangle","i","h"):
                plastic = (2*first_moment(rings,centroid,centroid[1]),2*first_moment(rings,centroid,centroid[0],axis=0))
        moment_scale = max(ix,iy,abs(ixy))
        principal = np.linalg.eigvalsh(np.array([[ix,-ixy],[-ixy,iy]])/moment_scale)[::-1]*moment_scale
        if principal[1] <= 0:
            invalid("polygon","section moments are nonpositive or numerically unresolved","input.out_of_range")
        r.add("area",a,"area")
        for name,val in (("centroid_x",centroid[0]),("centroid_y",centroid[1])):
            r.add(name,val,"length")
        for name,val in (("Ix",ix),("Iy",iy),("Ixy",ixy),("principal_I1",principal[0]),("principal_I2",principal[1]),("J_polar",ix+iy)):
            r.add(name,val,"inertia")
        r.add("principal_angle",.5*math.atan2(-2*ixy,ix-iy),"angle")
        r.data["principal_angle_unique"] = math.hypot(ix-iy,2*ixy) > 1e-12*(ix+iy)
        r.add("radius_x",math.sqrt(ix/a),"length")
        r.add("radius_y",math.sqrt(iy/a),"length")
        moduli = {"Sx_top":ix/(ymax-centroid[1]),"Sx_bottom":ix/(centroid[1]-ymin),"Sy_right":iy/(xmax-centroid[0]),"Sy_left":iy/(centroid[0]-xmin)}
        for name,val in moduli.items():
            r.add(name,val,"modulus")
        if torsion is not None:
            r.add("torsion_constant",torsion,"inertia")
        else:
            r.data["torsion_constant"] = None
            if kind == "polygon":
                r.warnings.append("General polygon torsion is not computed; J_polar is an area moment, not the Saint-Venant torsion constant.")
        if plastic is not None:
            r.add("plastic_Zx",plastic[0],"modulus")
            r.add("plastic_Zy",plastic[1],"modulus")
            r.add("shape_factor_x",plastic[0]/min(moduli["Sx_top"],moduli["Sx_bottom"]))
            r.add("shape_factor_y",plastic[1]/min(moduli["Sy_left"],moduli["Sy_right"]))
        at = q(at_height,"at_height","length",signed=True,optional=True)
        shear = q(shear_force,"shear_force","force",signed=True,optional=True)
        if shear is not None and at is None:
            invalid("at_height","shear flow requires a cut height")
        if at is not None:
            if not ymin <= at <= ymax:
                invalid("at_height","cut height must lie within the section")
            if circles:
                qm = 2/3*(max(radius**2-at**2,0)**1.5-max(inner**2-at**2,0)**1.5)
            else:
                qm = first_moment(rings,centroid,at)
            r.add("first_moment_Q",qm,"modulus")
            if shear is not None:
                r.add("shear_flow",shear*qm/ix,"line_load")
                r.warnings.append("VQ/I is the longitudinal shear resultant across this cut; it does not allocate flow among multiple webs or solve closed-cell shear flow.")
        return r.done("area")
    return run("section",inputs,compute)
