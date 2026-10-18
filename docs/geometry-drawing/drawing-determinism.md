# drawing_make_views: why the DXF and PDF bytes are reproducible

The design candidates measured that ezdxf output differs between processes started with different `PYTHONHASHSEED`, even with `ezdxf.options.write_fixed_meta_data_for_testing = True`. This file records what differs, the fix chosen and what it costs. The acceptance check is `test_the_dxf_and_the_pdf_are_the_same_bytes_under_every_hash_seed` in `tests/test_drawing_make_views.py`.

## What differed (measured)

A document with layers, two linetypes, the `ENG_ISO` dimstyle, lines, arcs, a circle, an ellipse, a polyline, text and two dimensions was written in subprocesses with `PYTHONHASHSEED` 1 to 12 and with the variable unset (ezdxf 1.4.5, Windows, Python 3.12).

* Two byte streams: seeds 4, 7 and 10 on one side, seeds 1, 2, 3, 5, 6, 8, 9, 11, 12 and unset on the other.
* A diff of the files shows one region: the CLASSES section, where the records `LAYOUT` (`AcDbLayout`) and `ACDBPLACEHOLDER` (`AcDbPlaceHolder`) swap places. Nothing else differs (no GUIDs, no dates, no handles, no OBJECTS section difference).
* Cause, in `ezdxf/sections/classes.py`: `ClassesSection.add_required_classes` ends with `for dxftype in dxf_types_in_use: self.add_class(dxftype)`, and `dxf_types_in_use()` returns a `set` of strings. Iterating a set of strings follows the string hash, which Python randomises per process.
* The earlier design notes placed the difference in the OBJECTS section. In this ezdxf version and for this document it is the CLASSES section; the order of objects was identical in all runs.
* The whole tool on the probe part (title, notes, front, top and right views, DXF and PDF), seeds 0 to 31 and unset, without the fix: 2 distinct DXF files (19 and 14 runs), 1 distinct PDF. The PDF does not depend on the seed because it is written from the recorded drawing in a fixed order, not from ezdxf's CLASS bookkeeping.

## Fix (chosen: in-process, option a)

`dxf._build` calls `doc.classes.add_required_classes(doc.dxfversion)` itself and then replaces `doc.classes.classes` with its items sorted by key `(name, cpp_class_name)`. When ezdxf saves, it calls `add_required_classes` again, but every class is already registered, so nothing is added and the sorted order stays. The order of CLASS records has no meaning in a DXF file (nothing refers to a class by its position), so sorting changes no semantics. `write_fixed_meta_data_for_testing` is set for the duration of one build under a lock and restored afterwards, because it is a process-wide option and tools run on a thread pool.

Result with the fix: the whole tool, seeds 0 to 31 and unset, 33 runs: 1 distinct DXF and 1 distinct PDF. The tests cover seeds 1 to 12 and unset, and an independent check that the CLASS records in the written file are in sorted order (`test_the_classes_section_is_in_a_fixed_order`), which fails as soon as another unordered source appears in that section.

## Cost

None measurable: the sort is over about 20 records. No child process was needed, so option (b) (a short-lived child with `PYTHONHASHSEED=0` for the writing step) was not built, and the owner decision "no worker process per call in v1" stands.

## Other things the bytes depend on

* The ezdxf version: `engine_versions` in the provenance records it.
* Fonts: text becomes glyph outlines in the PDF, so the PDF depends on the font files of the machine (Arial for Latin text, the first installed font of `conventions.FALLBACK_FONTS` for Chinese, Japanese and Korean text). The DXF holds only the font names. Two machines with different fonts give different PDF bytes for the same drawing; one machine gives one result.
* The model file, through `brep.project`, which orders its primitives totally (`_order`), so the result does not depend on the order OpenCASCADE returns edges in.
* The DXF text encoding is UTF-8 with `\n` line ends on every platform (it is written through a text buffer, not through the platform's file translation).
