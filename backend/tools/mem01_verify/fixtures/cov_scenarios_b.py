"""COV battery, part B — the delivered scenarios and the not-ready scenarios.

Role:
    The second half of the COV fixture battery: `DELIVERED` holds every scenario whose declared
    handoff is complete for its input kind, and `NOT_READY` holds every required input that is
    neither excluded by an independently established property nor delivered. This is where the
    "a component may not manufacture its own exemption" rule and the incomplete-handoff rules
    are pinned.

Used by:
    `tools.mem01_verify.fixtures.cov_scenarios` (assembles `COV_SCENARIOS`). Evaluators import
    `cov_scenarios.COV_SCENARIOS`, never this module directly.

Depends on:
    `tools.mem01_verify.fixtures.cov_scenarios_a` for the record types and factories. Nothing
    else — data only. Its EXPECTATIONS derive from `release/criteria.step1.v1.yaml` ->
    `scope_policy` (v0, `delivered_requires` and `not_ready` clauses) and Stage-A contract
    4.6 / 10.8, never from running an extractor or a parser (contract R12).

Key invariants:
    - Nothing in this module is excluded: `DELIVERED` records expect `delivered` and every
      `NOT_READY` record expects `not_ready`, so no record here carries an exclusion reason.
    - A `skipped_nondocument` status on a MIME class OUTSIDE `excluded_by_property` is not-ready,
      never excluded; an unexpected parser or extractor failure is not-ready, never excluded;
      truncated or partially extracted required content is not-ready even when text and full
      extractor provenance are stored — whether the truncation is declared by the status
      (`truncated`, `extracted_partial_scanned`) or by the stored partial marker
      `structured_truncated` (contract 16.17(c)).
    - `legacy_by_file_identity` is empty in Stage A, so a legacy `.doc` has no exclusion basis.
    - The remaining invariants (field semantics, folder policy, duplicates, expectation shape)
      are those of `cov_scenarios_a` and are not restated per record.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.cov_scenarios_a import (
    COMPLETE_HANDOFF,
    CovScenario,
    attachment_scenario,
    delivered_expectation,
    email_scenario,
    not_ready_expectation,
)

DELIVERED: tuple[CovScenario, ...] = (
    attachment_scenario(
        "cov-015",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "extracted",
        origin="delivered_requires.attachment satisfied in full: status + text + name + version.",
        expected=delivered_expectation("English memo.docx with a complete stored handoff."),
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-016",
        "application/pdf",
        "extracted",
        origin="Delivery is language-blind: a Bulgarian document with a full handoff is delivered.",
        expected=delivered_expectation(
            "Оферта.pdf, извлечена изцяло, с записан екстрактор и версия.",
        ),
        folder_policy="included:Изпратени",
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-017",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "extracted",
        origin="Spreadsheets are documents: no exclusion clause names them.",
        expected=delivered_expectation("Budget xlsx with a complete handoff."),
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-018",
        "application/pdf",
        "extracted",
        origin="inline_image needs an IMAGE MIME: an inline PDF is a required input, delivered.",
        expected=delivered_expectation(
            "Inline-disposed PDF with a complete handoff; is_inline alone excludes nothing.",
        ),
        is_inline=True,
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-019",
        "text/plain",
        "extracted",
        origin="text/plain is in neither exclusion list; a complete handoff delivers it.",
        expected=delivered_expectation("Plain-text notes attached by a partner.test sender."),
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-020",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "extracted",
        origin="A duplicate with its own complete handoff is delivered: dedup is not an exclusion.",
        expected=delivered_expectation("Byte-identical resend of cov-015; counted, not vanished."),
        duplicate_of="cov-015",
        folder_policy="included:Archive/2026",
        **COMPLETE_HANDOFF,
    ),
    email_scenario(
        "cov-021",
        "parsed",
        origin="delivered_requires.email: parsed status AND a body; extractor_* not consulted.",
        expected=delivered_expectation("Ordinary English message body from a1@acme.test."),
        body_present=True,
    ),
    email_scenario(
        "cov-022",
        "parsed",
        origin="scope_policy v0 declares no folder exclusion: a Spam-folder email is delivered.",
        expected=delivered_expectation(
            "Parsed body in the Spam folder; folder provenance decides nothing.",
        ),
        body_present=True,
        folder_policy="included:Spam",
    ),
    email_scenario(
        "cov-023",
        "parsed",
        origin="Same folder pin under Bulgarian mailbox naming; still no exclusion basis.",
        expected=delivered_expectation("Разпарсено тяло в папка Кошче — папката не изключва нищо."),
        body_present=True,
        folder_policy="included:Кошче",
    ),
)


NOT_READY: tuple[CovScenario, ...] = (
    attachment_scenario(
        "cov-024",
        "application/pdf",
        "skipped_nondocument",
        origin="The component may not invent an exemption: PDF is outside excluded_by_property.",
        expected=not_ready_expectation(
            "skipped_nondocument on a document MIME class — not ready, never excluded.",
        ),
    ),
    attachment_scenario(
        "cov-025",
        "application/zip",
        "skipped_nondocument",
        origin="Archives are NOT in the frozen exclusion list, so the skip cannot exempt them.",
        expected=not_ready_expectation(
            "Archive part the extractor skipped; policy v0 gives no exclusion basis.",
        ),
    ),
    attachment_scenario(
        "cov-026",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "skipped_nondocument",
        origin="Second non-excluded MIME class marked skipped_nondocument — same rule, other type.",
        expected=not_ready_expectation(
            "Spreadsheet skipped by the component; still a required input.",
        ),
    ),
    attachment_scenario(
        "cov-027",
        "application/pdf",
        "extracted",
        origin="delivered_requires needs extractor_version_present: no provenance is no delivery.",
        expected=not_ready_expectation(
            "Extracted text with an extractor_name but no extractor_version.",
        ),
        text_present=True,
        extractor_name_present=True,
    ),
    attachment_scenario(
        "cov-028",
        "application/pdf",
        "extracted",
        origin="Mirror of cov-027 for the missing extractor_name half of the handoff.",
        expected=not_ready_expectation("Extracted text with a version but no engine name."),
        text_present=True,
        extractor_version_present=True,
    ),
    attachment_scenario(
        "cov-029",
        "application/pdf",
        "extracted",
        origin="'extracted' without stored text fails text_present: a status is not a delivery.",
        expected=not_ready_expectation("Claimed extraction with a NULL/empty stored text handoff."),
        extractor_name_present=True,
        extractor_version_present=True,
    ),
    attachment_scenario(
        "cov-030",
        "application/pdf",
        "empty",
        origin="'empty' is in the not_ready list — an empty document is unfinished, not exempt.",
        expected=not_ready_expectation("Document that yielded no text at all."),
    ),
    attachment_scenario(
        "cov-031",
        "application/pdf",
        "truncated",
        origin="Truncated required content is NOT ready even with text and full provenance.",
        expected=not_ready_expectation(
            "PDF cut at the parse ceiling; a partial marker blocks delivery.",
        ),
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-032",
        "application/pdf",
        "scanned_pending_ocr",
        origin="OCR is out of v0 scope but a scan is still a required, undelivered document.",
        expected=not_ready_expectation(
            "Image-only PDF awaiting OCR; uncovered content, never an exclusion.",
        ),
    ),
    attachment_scenario(
        "cov-033",
        "application/pdf",
        "extracted_partial_scanned",
        origin="Partial extraction with image-only pages left over is not a complete handoff.",
        expected=not_ready_expectation("Born-digital text stored, scanned pages outstanding."),
        **COMPLETE_HANDOFF,
    ),
    attachment_scenario(
        "cov-034",
        "application/msword",
        "unsupported_format",
        origin="legacy_by_file_identity is empty in Stage A, so a .doc has no exclusion basis yet.",
        expected=not_ready_expectation("Legacy .doc with no extractor; accounted and undelivered."),
    ),
    attachment_scenario(
        "cov-035",
        "application/pdf",
        "corrupt",
        origin="An unexpected parser failure is NEVER an exclusion (contract 4.6, ruling (a)).",
        expected=not_ready_expectation("Damaged PDF the extractor could not read."),
    ),
    attachment_scenario(
        "cov-036",
        "application/pdf",
        "encrypted",
        origin="'encrypted' is in the not_ready list: a locked document is undelivered content.",
        expected=not_ready_expectation("Password-protected PDF."),
    ),
    attachment_scenario(
        "cov-037",
        "application/pdf",
        "pending",
        origin="Never-processed required inputs are not ready, not silently out of scope.",
        expected=not_ready_expectation("Document still queued for extraction."),
    ),
    attachment_scenario(
        "cov-038",
        "application/pdf",
        "skipped_oversize",
        origin="'skipped_oversize' is in the not_ready list — a size skip is not an exemption.",
        expected=not_ready_expectation("Payload above the parse ceiling; content stays uncovered."),
    ),
    attachment_scenario(
        "cov-039",
        "application/octet-stream",
        "unsupported_format",
        origin="is_inline without an image MIME matches no exclusion clause at all.",
        expected=not_ready_expectation(
            "Inline octet-stream part: no exclusion basis, no delivery either.",
        ),
        is_inline=True,
    ),
    attachment_scenario(
        "cov-040",
        "application/pdf",
        "scanned_pending_ocr",
        origin="A duplicate of a not-ready input is itself not ready — dedup completes no handoff.",
        expected=not_ready_expectation("Byte-identical resend of cov-032, still awaiting OCR."),
        duplicate_of="cov-032",
        folder_policy="included:Изпратени",
    ),
    attachment_scenario(
        "cov-044",
        None,
        "skipped_nondocument",
        origin="No declared content_type = no property to match: nothing establishes an exclusion.",
        expected=not_ready_expectation(
            "Part with a NULL declared MIME type; unclassified is not exempt.",
        ),
    ),
    attachment_scenario(
        "cov-045",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "extracted",
        origin=(
            "Contract 4.6 / 16.17(c): delivered_requires.attachment carries "
            "partial_marker_absent, so a stored truncation marker blocks delivery even when the "
            "status, the text and both extractor fields are all present."
        ),
        expected=not_ready_expectation(
            "Workbook stored with extracted_data.truncated = true: a partial extraction is "
            "uncovered content, never a delivered handoff.",
        ),
        structured_truncated=True,
        **COMPLETE_HANDOFF,
    ),
    email_scenario(
        "cov-041",
        "failed",
        origin="A parse failure on an email is not ready and can never become an exclusion.",
        expected=not_ready_expectation("Malformed MIME the parser refused; no body arrived."),
        body_present=False,
    ),
    email_scenario(
        "cov-042",
        "parsed",
        origin="delivered_requires.email needs BOTH a parsed status and a present body.",
        expected=not_ready_expectation("Parsed envelope with a NULL body (attachment-only mail)."),
        body_present=False,
    ),
    email_scenario(
        "cov-043",
        "failed",
        origin="Folder provenance cannot rescue a parse failure either — same rule, BG folder.",
        expected=not_ready_expectation(
            "Провалено парсване в папка Изпратени — не е готово, не е изключено.",
        ),
        body_present=False,
        folder_policy="included:Изпратени",
    ),
)
