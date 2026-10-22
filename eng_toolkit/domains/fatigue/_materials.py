"""Small orientation table, never used as automatic design input.

Spans cover the stated conditions, not scatter or guaranteed allowable values.
Equal endpoints mean a nominal reference value, not zero uncertainty.
"""

CAST_SOURCE = "Acracast, Alloy Mechanical Properties, carbon and low alloy steels; https://www.acracast.com/alloy-properties.html"
PHYSICAL_SOURCE = "Budynas and Nisbett, Shigley's Mechanical Engineering Design, 11th ed., Table A-5 (generic room-temperature metal constants)"
KSI_MPA = 6.894757293168361


def steel(name, condition, yield_ksi, ultimate_ksi, elongation):
    return dict(material=name, condition=condition, E_GPa=[207, 207], poisson=[.29, .29], density_kg_m3=[7850, 7850],
                yield_MPa=[x*KSI_MPA for x in yield_ksi], ultimate_MPa=[x*KSI_MPA for x in ultimate_ksi],
                elongation_percent=elongation, source=CAST_SOURCE, physical_source=PHYSICAL_SOURCE,
                basis="published reference strengths across listed casting heat treatments; not wrought-product allowables")


TYPICAL = [
    steel("1020", "investment cast, annealed through normalized and drawn", [37.5,45], [57.5,65], [20,28]),
    steel("1045", "investment cast, annealed through quenched and drawn", [40,95], [75,140], [7,20]),
    steel("4140", "investment cast, annealed through quenched and tempered at 350 F", [60,200], [90,220], [4,17]),
    dict(material="304", condition="Core 304/4301, annealed flat products at room temperature", E_GPa=[200,200], poisson=[.30,.30],
         density_kg_m3=[7900,7900], yield_MPa=[210,230], ultimate_MPa=[520,750], elongation_percent=[45,45],
         source="Outokumpu Core range datasheet, 304/4301 hot/cold strip rows; https://www.outokumpu.com/en/products/product-ranges/core",
         physical_source=PHYSICAL_SOURCE, basis="span of product specification reference values; elongation is a listed minimum, not a measured distribution"),
    dict(material="6061-T6", condition="Hydro T6/T6511 extrusions, thickness <=25 mm", E_GPa=[69,69], poisson=[.33,.33],
         density_kg_m3=[2700,2700], yield_MPa=[240,240], ultimate_MPa=[260,260], elongation_percent=[8,10],
         source="Hydro Alloy 6061 datasheet, T6/T6511 rows; https://www.hydro.com/Document/Index?id=560718&name=Alloy+6061.pdf",
         physical_source=PHYSICAL_SOURCE, basis="rounded physical constants and published minimum reference strength/elongation values"),
    dict(material="7075-T6", condition="Kaiser T6/T651 room-temperature nominal sheet/plate properties", E_GPa=[71,71], poisson=[.33,.33],
         density_kg_m3=[2800,2800], yield_MPa=[503,503], ultimate_MPa=[572,572], elongation_percent=[11,11],
         source="Kaiser Aluminum, Sheet Coil & Plate Alloy 7075, typical mechanical properties, Rev 05/06; https://online.kaiseraluminum.com/depot/PublicProductInformation/Document/1017/Kaiser_Aluminum_7075_Sheet_Coil_and_Plate.pdf",
         physical_source=PHYSICAL_SOURCE, basis="typical nominal values; equal endpoints are not a tolerance interval"),
    dict(material="Ti-6Al-4V", condition="TIMETAL 6-4 annealed through solution treated and aged, room temperature", E_GPa=[107,122], poisson=[.31,.31],
         density_kg_m3=[4420,4420], yield_MPa=[827,1068], ultimate_MPa=[895,1137], elongation_percent=[10,10],
         source="TIMET datasheet TMC-0150, Tables 2 and 3; https://www.timet.com/documents/datasheets/alpha-and-beta-alloys/timetal-6-4.pdf",
         physical_source="TIMET TMC-0150 Table 2", basis="span across listed product/heat-treatment minimum reference values; E depends on texture"),
    dict(material="grey_iron", condition="Dura-Bar G2 pearlitic grey iron, room temperature", E_GPa=[124,124], poisson=None,
         density_kg_m3=[7060,7340], yield_MPa=None, ultimate_MPa=[40*KSI_MPA,40*KSI_MPA], elongation_percent=None,
         source="Dura-Bar G2 product sheet; https://www.dura-bar.com/products/gray-iron/g2",
         physical_source="Dura-Bar G2 manufacturer-supplied physical properties; Poisson ratio not supplied",
         basis="tensile reference minimum 40 ksi; brittle material has no conventional tensile yield/elongation; unavailable properties are null"),
]
