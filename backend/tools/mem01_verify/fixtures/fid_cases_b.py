"""
Role: The FID synthetic ORIGINAL library — the format-independent `DocSpec` documents every
      per-format battery renders. One ORIGINAL, seven renderings: authoring the document once and
      deriving its expectation once (`expectation_for_doc`) is what keeps the expected units tied
      to the ORIGINAL rather than to any extractor (contract R12). Twelve Latin-script ORIGINALS
      (usable in every format including PDF, whose base font is WinAnsi) and six Bulgarian ones.
Used by: `fid_cases_c.py` (pdf, docx), `fid_cases_d.py` (xlsx, tnef), `fid_cases_e.py` (html, rtf,
         plain text), `fid_cases_f.py` (encoding/Unicode cases reuse a few of these ORIGINALS).
Depends on: `fid_cases_a` for `DocSpec`/`TableSpec`/`LinkPair` and the frozen NBSP constants.
            Nothing from `backend/app/`, nothing from `backend/tests/`.
Key invariants:
  - LATIN_ORIGINALS contain only scalars the WinAnsi base-14 encoding can express, so a PDF
    ORIGINAL never needs an embedded font; CYRILLIC_ORIGINALS carry the Bulgarian half of the
    battery and are never rendered to PDF.
  - Every ORIGINAL that carries a table carries a HEADER ASSOCIATION that a column swap would
    break: two columns of the same shape (two counterparties, two prices), so a swapped pair is
    detectable from the flat text alone.
  - The negation cells are literal: "НЕ е платено" and "NOT paid". Their guards are derived by
    `expectation_for_doc` from the frozen negation prefixes, never restated by hand.
  - Grouped-number values carry NBSP (U+00A0) as the thousands separator, with the plain-space
    rendering declared as a frozen alternative and the SEPARATOR-DELETED rendering declared
    forbidden — losing a separator changes the value, so it is a defect, not a whitespace variant.
  - Addresses and hosts only under `example.test`, `acme.test`, `partner.test`; every name,
    number, invoice id and amount is invented.
"""

from __future__ import annotations

from tools.mem01_verify.fixtures.fid_cases_a import NBSP, DocSpec, LinkPair, TableSpec

#: Grouped amounts of the ORIGINALS: NBSP thousands separator, comma decimal separator.
AMOUNT_BG = f"1{NBSP}250,00 лв."
AMOUNT_BG_ALT = "1 250,00 лв."
AMOUNT_BG_COLLAPSED = "1250,00 лв."
AMOUNT_EN = f"12{NBSP}480,50 EUR"
AMOUNT_EN_ALT = "12 480,50 EUR"
AMOUNT_EN_COLLAPSED = "12480,50 EUR"

#: The frozen whitespace alternatives shared by every ORIGINAL that carries a grouped amount.
_AMOUNT_ALTERNATIVES = {AMOUNT_BG: (AMOUNT_BG_ALT,), AMOUNT_EN: (AMOUNT_EN_ALT,)}

#: Renderings that must NEVER occur: deleting a separator changes the value.
COLLAPSED_AMOUNTS: tuple[str, ...] = (AMOUNT_BG_COLLAPSED, AMOUNT_EN_COLLAPSED)


# ── Latin-script ORIGINALS (renderable in every format, PDF included) ──

EN_INVOICE = DocSpec(
    paragraphs=("Invoice summary for order ORD-4417.", "Prepared by the billing desk."),
    bullets=("Delivery accepted on site.", "Two pallets were returned."),
    tables=(
        TableSpec(
            caption="Settlement per counterparty",
            headers=("Counterparty", "Amount", "Status"),
            rows=(
                ("Acme Trading", AMOUNT_EN, "Paid"),
                ("Partner Logistics", "9 900,00 EUR", "NOT paid"),
            ),
            sheet="Settlement",
        ),
    ),
    links=(LinkPair("Open the settlement portal", "https://portal.acme.test/settlement/4417"),),
    trailing=("Questions go to billing@acme.test.",),
    alternatives=_AMOUNT_ALTERNATIVES,
)

EN_REPORT = DocSpec(
    paragraphs=("Quarterly delivery report.", "Scope: two warehouses, one carrier."),
    bullets=("Warehouse A reached the target.", "Warehouse B missed one window."),
    tables=(
        TableSpec(
            caption="Deliveries per site",
            headers=("Site", "Planned", "Delivered"),
            rows=(("Site north", "120", "118"), ("Site south", "140", "131")),
            sheet="Deliveries",
        ),
    ),
    links=(LinkPair("Full report", "https://reports.partner.test/q3/deliveries"),),
    trailing=("Prepared for the operations review.",),
)

EN_HEADER_ASSOCIATION = DocSpec(
    paragraphs=("Two counterparties, two prices, one row each.",),
    tables=(
        TableSpec(
            caption="Price per counterparty",
            headers=("Acme price", "Partner price"),
            rows=(("410,00 EUR", "395,00 EUR"), ("870,00 EUR", "902,00 EUR")),
            sheet="Prices",
        ),
    ),
    trailing=("A price under the other counterparty's header is a defect, not a rounding.",),
)

EN_LIST_ORDER = DocSpec(
    paragraphs=("Handover checklist, in the order the steps must be performed.",),
    bullets=(
        "Step one: confirm the pallet count.",
        "Step two: sign the transfer note.",
        "Step three: archive the scan.",
        "Step four: notify the billing desk.",
    ),
    trailing=("The order of the steps is part of the document.",),
)

EN_LINKS = DocSpec(
    paragraphs=("Three destinations, three labels; no label names its own destination.",),
    links=(
        LinkPair("Portal", "https://portal.acme.test/login"),
        LinkPair("Handbook", "https://docs.partner.test/handbook"),
        LinkPair("Support desk", "https://support.example.test/tickets/new"),
    ),
    trailing=("A label without its destination is a lost unit.",),
)

EN_NUMBERS = DocSpec(
    paragraphs=("Grouped amounts keep their separators.",),
    tables=(
        TableSpec(
            caption="Cost breakdown",
            headers=("Line", "Amount"),
            rows=(("Goods", AMOUNT_EN), ("Freight", "1 010,00 EUR")),
            sheet="Amounts",
        ),
    ),
    trailing=("Deleting a thousands separator changes the value.",),
    alternatives=_AMOUNT_ALTERNATIVES,
)

EN_MIXED_ROW = DocSpec(
    paragraphs=("A row whose cells are of different shapes must stay one row.",),
    tables=(
        TableSpec(
            caption="Mixed row",
            headers=("Reference", "Quantity", "Note"),
            rows=(
                ("REF-8801", "17", "NOT approved"),
                ("REF-8802", "4", "Approved on site"),
            ),
            sheet="Mixed",
        ),
    ),
)

EN_MINIMAL = DocSpec(
    paragraphs=("A single paragraph document.",),
)

EN_TRAILING = DocSpec(
    paragraphs=("The opening paragraph.",),
    bullets=("The only list item.",),
    trailing=("The closing paragraph, which must not migrate to the top.",),
)

EN_LONG_PARAGRAPH = DocSpec(
    paragraphs=(
        "This paragraph contains (parentheses), a backslash \\ and a slash / so the "
        "string-escaping of every container is exercised without changing a single scalar.",
    ),
    trailing=("The escaped characters must survive as themselves.",),
)

EN_TWO_TABLES = DocSpec(
    paragraphs=("Two tables in one document; the second must not absorb the first.",),
    tables=(
        TableSpec(
            caption="First table",
            headers=("Item", "Count"),
            rows=(("Boxes", "12"), ("Pallets", "3")),
            sheet="First",
        ),
        TableSpec(
            caption="Second table",
            headers=("Article", "Units"),
            rows=(("Crates", "7"), ("Rolls", "21")),
            sheet="Second",
        ),
    ),
)

EN_SPARSE_ROW = DocSpec(
    paragraphs=("A row with an explicit placeholder cell must not close the gap.",),
    tables=(
        TableSpec(
            caption="Sparse row",
            headers=("Code", "Owner", "Amount"),
            rows=(("C-1", "n/a", "480,00 EUR"), ("C-2", "Acme Trading", "none")),
            sheet="Sparse",
        ),
    ),
)

#: Twelve Latin-script ORIGINALS, in battery order.
LATIN_ORIGINALS: tuple[tuple[str, DocSpec], ...] = (
    ("invoice with a NOT-paid cell and a portal link", EN_INVOICE),
    ("quarterly report with a planned/delivered table", EN_REPORT),
    ("two same-shaped price columns (header association)", EN_HEADER_ASSOCIATION),
    ("ordered handover checklist", EN_LIST_ORDER),
    ("three link labels with foreign destinations", EN_LINKS),
    ("grouped amounts with NBSP thousands separators", EN_NUMBERS),
    ("row of mixed-shape cells carrying a negation", EN_MIXED_ROW),
    ("single-paragraph minimal document", EN_MINIMAL),
    ("opening paragraph, list item and closing paragraph", EN_TRAILING),
    ("paragraph exercising container string escaping", EN_LONG_PARAGRAPH),
    ("two tables that must not merge", EN_TWO_TABLES),
    ("row with an explicit placeholder cell", EN_SPARSE_ROW),
)


# ── Bulgarian ORIGINALS (every format except PDF) ──

BG_INVOICE = DocSpec(
    paragraphs=("Обобщение по фактура No 2210/17.", "Изготвено от отдел Разчети."),
    bullets=("Доставката е приета на място.", "Два палета са върнати."),
    tables=(
        TableSpec(
            caption="Разчети по контрагент",
            headers=("Контрагент", "Сума", "Статус"),
            rows=(
                ("Акме ООД", AMOUNT_BG, "Платено"),
                ("Партнер ЕАД", "9 900,00 лв.", "НЕ е платено"),
            ),
            sheet="Разчети",
        ),
    ),
    links=(LinkPair("Отвори портала за разчети", "https://portal.acme.test/razcheti/2210"),),
    trailing=("Въпроси на адрес razcheti@acme.test.",),
    alternatives=_AMOUNT_ALTERNATIVES,
)

BG_PROTOCOL = DocSpec(
    paragraphs=("Протокол от приемане на обект.", "Присъстват: доставчик и възложител."),
    bullets=("Обектът е предаден в срок.", "Забележките са отстранени."),
    tables=(
        TableSpec(
            caption="Позиции по протокола",
            headers=("Позиция", "Количество", "Приета"),
            rows=(("Профили", "34", "Да"), ("Крепежи", "120", "НЕ е приета")),
            sheet="Протокол",
        ),
    ),
    links=(LinkPair("Сканиран протокол", "https://docs.partner.test/protokoli/2210"),),
    trailing=("Протоколът се архивира в срок от пет дни.",),
)

BG_HEADER_ASSOCIATION = DocSpec(
    paragraphs=("Две колони с еднакви по вид стойности — размяната им е дефект.",),
    tables=(
        TableSpec(
            caption="Цена по контрагент",
            headers=("Цена Акме", "Цена Партнер"),
            rows=(("410,00 лв.", "395,00 лв."), ("870,00 лв.", "902,00 лв.")),
            sheet="Цени",
        ),
    ),
    trailing=("Стойност под чужд header е загубена асоциация.",),
)

BG_LIST_ORDER = DocSpec(
    paragraphs=("Контролен списък в реда, в който стъпките се изпълняват.",),
    bullets=(
        "Стъпка едно: потвърди броя палети.",
        "Стъпка две: подпиши приемо-предавателния протокол.",
        "Стъпка три: архивирай сканираното копие.",
        "Стъпка четири: уведоми отдел Разчети.",
    ),
    trailing=("Редът на стъпките е част от документа.",),
)

BG_LINKS = DocSpec(
    paragraphs=("Три адреса и три етикета; нито един етикет не съдържа адреса си.",),
    links=(
        LinkPair("Портал", "https://portal.acme.test/vhod"),
        LinkPair("Наръчник", "https://docs.partner.test/narachnik"),
        LinkPair("Поддръжка", "https://support.example.test/zayavki"),
    ),
    trailing=("Етикет без адреса си е изгубена единица.",),
)

BG_NUMBERS = DocSpec(
    paragraphs=("Групираните суми запазват разделителите си.",),
    tables=(
        TableSpec(
            caption="Суми",
            headers=("Перо", "Сума"),
            rows=(("Стоки", AMOUNT_BG), ("Транспорт", "1 010,00 лв.")),
            sheet="Суми",
        ),
    ),
    trailing=("Изтриването на разделител променя стойността.",),
    alternatives=_AMOUNT_ALTERNATIVES,
)

#: Six Bulgarian ORIGINALS, in battery order.
CYRILLIC_ORIGINALS: tuple[tuple[str, DocSpec], ...] = (
    ("фактура с клетка „НЕ е платено“ и линк към портала", BG_INVOICE),
    ("приемо-предавателен протокол с отрицание в таблицата", BG_PROTOCOL),
    ("две еднотипни ценови колони (асоциация със заглавието)", BG_HEADER_ASSOCIATION),
    ("подреден контролен списък", BG_LIST_ORDER),
    ("три етикета на линкове с чужди адреси", BG_LINKS),
    ("групирани суми с неразделим интервал", BG_NUMBERS),
)

#: The two-column page layout of the ORIGINAL: reading order is column one top to bottom, then
#: column two. A reader that sorts by vertical position alone interleaves them — a real defect.
TWO_COLUMN_LEFT: tuple[str, ...] = (
    "Left column, first block.",
    "Left column, second block.",
    "Left column, third block.",
)
TWO_COLUMN_RIGHT: tuple[str, ...] = (
    "Right column, first block.",
    "Right column, second block.",
    "Right column, third block.",
)


def boundary_sequence(spec: DocSpec) -> tuple[str, ...]:
    """The explicit page/sheet boundary constraint of an ORIGINAL split before its first table.

    Every format that can carry a real boundary (a PDF page break, a docx page break, a workbook
    sheet change) puts the paragraphs and list items before it and the first table after it. The
    constraint names the LAST unit before the boundary and the FIRST unit after it, so the
    boundary is a stated property of the ORIGINAL rather than an accident of the document flow.

    Args:
        spec: the ORIGINAL; it must carry at least one table.

    Returns:
        The two unit ids the boundary separates, in reading order.
    """
    last_before = f"b{len(spec.bullets)}" if spec.bullets else f"p{len(spec.paragraphs)}"
    return (last_before, "t1cap")
