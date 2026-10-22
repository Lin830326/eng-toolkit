"""Compact record schemas shared by the three frame tools."""

from typing import Annotated

from pydantic import Field

Nodes = Annotated[str, Field(description="Nodes 'id,x,y,z', e.g. 'a,0 m,0 m,0 m;b,2 m,0 m,0 m'; semicolon/newline records; 200 max.")]
Members = Annotated[str, Field(description="Members 'id,i,j,E,G,A,Iy,Iz,J', e.g. 'e,a,b,200 GPa,80 GPa,1e-3 m^2,1e-6 m^4,2e-6 m^4,1e-6 m^4'; 400 max.")]
Supports = Annotated[str, Field(description="Restraints 'a,fixed', 'a,pinned', 'a,ux+rz'; springs 'a,spring,ux,1 kN/m,0 m'; '' omits; see docs.")]
Loads = Annotated[str, Field(description="Nodal 'id,Fx,Fy,Fz,Mx,My,Mz', e.g. 'b,1 kN,0 N,0 N,0 N*m,0 N*m,0 N*m'; '' omits; 800 max.")]
MemberLoads = Annotated[str, Field(description="Member 'e,uniform,local,0 N/m,1 kN/m,0 N/m' or 'e,point,global,1 m,0 N,1 kN,0 N'; '' omits; 800 max.")]
Orientations = Annotated[str, Field(description="Local-y reference 'e,vector,0,1,0' or roll 'e,roll,30 deg'; '' uses projected global Y, then Z for vertical Y.")]
Releases = Annotated[str, Field(description="Local end releases 'e,start,ry+rz' or 'e,end,rx+ry+rz'; torsion/bending rotations only; '' rigid ends.")]
Shear = Annotated[str, Field(description="Effective shear areas 'e,Asy,Asz', e.g. 'e,800 mm^2,600 mm^2'; '' uses Euler-Bernoulli; G comes from members.")]
Thermal = Annotated[str, Field(description="Axial thermal strain 'e,alpha,dT', e.g. 'e,12e-6 1/K,20 K'; '' omits; differences reject offset temperatures.")]
Subdivisions = Annotated[int, Field(description="Equal subelements per original member, e.g. 4; integer 1..20; refined frame limited to 1200 active DOFs.")]
Modes = Annotated[int, Field(description="Number of lowest modes to return, e.g. 6; integer 1..30; limited by remaining degrees of freedom.")]
