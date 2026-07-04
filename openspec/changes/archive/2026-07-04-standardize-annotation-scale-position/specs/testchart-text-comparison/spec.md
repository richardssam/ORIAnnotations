## ADDED Requirements

### Requirement: Automated Text Annotation Comparison

The testchart comparison harness SHALL verify text annotation rendering by measuring, for each known text label, whether annotation-coloured ink appears starting at the expected left-baseline anchor position in the rendered PNG output. The expected pixel position SHALL be computed from the label's OTIO-normalized position via `coords.otio_to_px`.

Because text is left-baseline anchored — it extends *upward* (ascent) and *rightward* from the anchor, not outward symmetrically — a centred square window is the wrong shape (a large font's ascent alone can exceed a 20px window). The comparison SHALL instead scan an asymmetric region sized from the label's known font pixel height (covering ascent above the baseline and a small descender allowance below, plus a generous rightward run and a small leftward overshoot tolerance) and locate the *leftmost* column containing matching-colour ink — i.e. whether the text actually starts where expected.

#### Scenario: Text label lands at expected anchor

- **WHEN** the comparison runs against a rendered testchart frame containing a text label
- **THEN** it SHALL scan a region spanning `[px − x_tolerance, px + right_margin]` horizontally and `[py − font_px*1.1, py + font_px*0.3]` vertically (ascent-biased, sized from the label's known font pixel height)
- **AND** report the horizontal offset between the expected anchor `px` and the leftmost column containing matching-colour ink

#### Scenario: Anchor within tolerance passes

- **WHEN** matching-colour ink is found in the scanned region and the leftmost such column lands within ±5 px (scaled by resolution) of the expected anchor
- **THEN** the comparison SHALL report the text annotation as a PASS

#### Scenario: Missing or displaced label fails

- **WHEN** no matching-colour ink is found anywhere in the scanned region, or the leftmost matching column's offset exceeds the resolution-scaled tolerance
- **THEN** the comparison SHALL report the text annotation as a FAIL with the measured offset

### Requirement: Text Comparison Integrated Into Harness

The text comparison SHALL extend the existing `compare_testchart.py` / `compare_thickness.py` infrastructure and run as part of the full testchart batch, contributing to the overall pass/fail result for both the RV and xStudio render paths.

#### Scenario: Text comparison runs in the batch

- **WHEN** the full testchart batch is executed
- **THEN** the text annotation comparison SHALL run alongside stroke comparison
- **AND** a text failure SHALL cause the overall batch comparison to fail
