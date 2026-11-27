# Machine elements (`machine`)

[All domains (English)](../../README.md#domains) · [全部領域（繁體中文）](../../README.zh-TW.md#領域與工具)

## Machine elements (`machine`)

Six unit-checked tools cover shafts (`machine_shaft`), spur gears (`machine_gear`), fillet weld groups (`machine_weld`), elastic cylinders/spheres (`machine_pressure_vessel`), belt drives (`machine_belt`), and keys/splines (`machine_key`). Results include formulas, assumptions, validity limits and `review_status: "unreviewed"`. These are textbook estimates, including Marin fatigue, Lewis bending, Hertz contact and Lamé stresses. They are not AGMA/ISO gear ratings or ASME pressure-vessel code checks. See [the machine domain design](../machine/design.md) for inputs, equations and verification.

`machine_shaft` builds its static block (`von_mises`, `static_safety_factor`) from `bending_moment`, `torque` and `axial_force`. When none of the three is given, the static block uses the maximum of the fatigue loads, `|mean| + alternating`, and says so in `data.static_load_basis` and in a warning. When static and fatigue loads are both given, the static loads are used alone, and `peak_von_mises` and `peak_safety_factor` add the yield check at the maximum fatigue load.

`machine_gear` has no helix angle and returns `d = m N`. For a helical gear, pass the transverse module `m_n/cos(beta)`.
