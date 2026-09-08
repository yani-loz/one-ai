"""
Role: The encoding / Unicode half of the FID fixture battery — `fid-091..124`, thirty-four cases
      covering cp1251-declared payloads, UTF-8 with a byte-order mark, mixed scripts, non-breaking
      spaces inside numbers, combining marks and the typographic scalars a text pipeline is most
      likely to silently change. Every expectation is the ORIGINAL's own scalars (contract R12,
      section 10.5).
Used by: `fid_cases.py` (concatenates every half into `build_fid_cases()`).
Depends on: `fid_cases_a` (record vocabulary and the frozen mojibake / BOM constants),
            `fid_builders_b` (docx and xlsx assemblers), `fid_builders_c` (html / rtf / text
            renderers). Nothing from `backend/app/` or `backend/tests/`.
Key invariants:
  - The DEFECT SIGNATURES are frozen negatives, not guesses: no case's ORIGINAL contains U+FFFD or
    the mojibake lead scalars (`Ã`, `Ð`, `Â`), so their appearance in the stored text is proof a
    decode went wrong; and no ORIGINAL means its BOM as content, so a surviving U+FEFF is proof a
    signature was stored as text.
  - A LYING charset declaration is a property of exactly the cases that say so in their `origin`.
    The expectation is still the ORIGINAL's text: a declaration that contradicts the bytes does
    not change what the document says, so recovering the text is the right answer and failing to
    is a fidelity defect.
  - NBSP (U+00A0) and NARROW NBSP (U+202F) inside a grouped number may render as a plain space
    (a frozen whitespace alternative), but DELETING them is forbidden — that changes the value.
  - A decomposed sequence (base + combining mark) is what the ORIGINAL contains; silently
    normalizing it to the precomposed form alters the stored scalars and is a defect, so the
    decomposed literal is the required unit.
  - Every ORIGINAL here is one to three short lines, so a failure names exactly one scalar class.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tools.mem01_verify.fixtures.fid_builders_b import build_docx, xlsx_from_doc
from tools.mem01_verify.fixtures.fid_builders_c import html_from_doc, rtf_from_doc, text_from_doc
from tools.mem01_verify.fixtures.fid_cases_a import (
    BOM_SCALAR,
    MOJIBAKE_MARKERS,
    NARROW_NBSP,
    NBSP,
    DocSpec,
    FidCase,
    FidExpectation,
    FidFormat,
    FidUnit,
    fid_case,
)

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
XLSX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

#: The UTF-8 byte-order mark as bytes (a signature; never part of the ORIGINAL's content).
UTF8_BOM = b"\xef\xbb\xbf"


@dataclass(frozen=True)
class _EncodingSample:
    """One encoding ORIGINAL: its lines, the format it is authored in and how it is encoded."""

    origin: str
    lines: tuple[str, ...]
    fid_format: FidFormat = "text"
    encoding: str = "utf-8"
    declared_charset: str = "utf-8"
    bom: bool = False
    newline: str = "\n"
    forbidden: tuple[str, ...] = ()
    alternatives: dict[str, tuple[str, ...]] = field(default_factory=dict)


def _payload(sample: _EncodingSample) -> tuple[bytes, str]:
    """Render one encoding sample to bytes and to the media type it is declared under."""
    spec = DocSpec(paragraphs=sample.lines, alternatives=sample.alternatives)
    if sample.fid_format == "html":
        body = html_from_doc(spec, charset=sample.declared_charset, encoding=sample.encoding)
        return (UTF8_BOM + body if sample.bom else body), "text/html"
    if sample.fid_format == "rtf":
        return rtf_from_doc(spec), "text/rtf"
    if sample.fid_format == "docx":
        return build_docx(spec), DOCX_CONTENT_TYPE
    if sample.fid_format == "xlsx":
        return xlsx_from_doc(spec), XLSX_CONTENT_TYPE
    body = text_from_doc(spec, encoding=sample.encoding, newline=sample.newline)
    media = f"text/plain; charset={sample.declared_charset}"
    return (UTF8_BOM + body if sample.bom else body), media


def _expectation(sample: _EncodingSample) -> FidExpectation:
    """The expectation of one encoding sample: its own lines, in order, and the frozen negatives."""
    units = tuple(
        FidUnit(f"e{index}", "paragraph", line, sample.alternatives.get(line, ()))
        for index, line in enumerate(sample.lines, start=1)
    )
    flow = tuple(unit.unit_id for unit in units)
    return FidExpectation(
        units=units,
        ordered_sequences=((flow,) if len(flow) > 1 else ()),
        forbidden=(*MOJIBAKE_MARKERS, BOM_SCALAR, *sample.forbidden),
    )


# ── The grouped-amount literals of this battery (NBSP and narrow NBSP separators) ──
_NBSP_BG = f"1{NBSP}250,00 лв."
_NBSP_BG_ALT = "1 250,00 лв."
_NBSP_EN = f"12{NARROW_NBSP}480,50 EUR"
_NBSP_EN_ALT = "12 480,50 EUR"
_NBSP_ALTERNATIVES = {_NBSP_BG: (_NBSP_BG_ALT,), _NBSP_EN: (_NBSP_EN_ALT,)}
_NBSP_FORBIDDEN = ("1250,00 лв.", "12480,50 EUR")

_NBSP_BG_LINE = f"Дължима сума: {_NBSP_BG}"
_NBSP_EN_LINE = f"Amount due: {_NBSP_EN}"
_NBSP_COMBINED = f"„Позиция\u0301“ на стойност {_NBSP_BG} — приета."

# ── The decomposed literals (base scalar + combining mark) ──
_DECOMPOSED_BG = "Строи\u0306-Инвест ООД потвърди срока."
_DECOMPOSED_LATIN = "Re\u0301sume\u0301 of the delivery window."
_DECOMPOSED_MIX = "Прието\u0301 и NOT approved."
_NORMALIZATION_PROBE = "R\u00e9sum\u00e9 and Re\u0301sume\u0301 are different strings."

_CP1251_SAMPLES: tuple[_EncodingSample, ...] = (
    _EncodingSample(
        "cp1251 bytes declared windows-1251: a Bulgarian invoice line must arrive as itself",
        ("Фактура No 2210/17 към Акме ООД.",),
        encoding="cp1251",
        declared_charset="windows-1251",
    ),
    _EncodingSample(
        "cp1251 bytes declared under the cp1251 alias spelling",
        ("Плащането е прието по банков път.",),
        encoding="cp1251",
        declared_charset="cp1251",
    ),
    _EncodingSample(
        "cp1251 html declaring windows-1251 in its meta tag",
        ("Разчети по контрагент за месец март.",),
        fid_format="html",
        encoding="cp1251",
        declared_charset="windows-1251",
    ),
    _EncodingSample(
        "cp1251 html carrying Bulgarian typographic quotes (0x84 / 0x93 in that code page)",
        ("Позицията е отбелязана като „НЕ е платено“ в протокола.",),
        fid_format="html",
        encoding="cp1251",
        declared_charset="windows-1251",
    ),
    _EncodingSample(
        "rtf declaring ansicpg1251 while carrying its Cyrillic as escapes",
        ("Протокол за приемане на обект.",),
        fid_format="rtf",
    ),
    _EncodingSample(
        "LYING declaration: cp1251 bytes declared as utf-8 — the text still says what it says, so "
        "the ORIGINAL's scalars are the right answer",
        ("Доставката е приета без забележки.",),
        encoding="cp1251",
        declared_charset="utf-8",
    ),
)

_BOM_SAMPLES: tuple[_EncodingSample, ...] = (
    _EncodingSample(
        "UTF-8 with a byte-order mark: the BOM is a signature and must not survive as content",
        ("Приложение към фактура 2210/17.",),
        bom=True,
    ),
    _EncodingSample(
        "UTF-8 BOM on an English line",
        ("Attached is the settlement summary.",),
        bom=True,
    ),
    _EncodingSample(
        "UTF-8 BOM before a separator-joined row: the BOM must not glue itself to the first cell",
        ("Контрагент | Сума | Статус", "Акме ООД | 410,00 лв. | НЕ е платено"),
        bom=True,
    ),
    _EncodingSample(
        "UTF-8 BOM at the head of an html document",
        ("Обобщение на доставките за седмицата.",),
        fid_format="html",
        bom=True,
    ),
    _EncodingSample(
        "UTF-8 BOM with CRLF line endings: neither the BOM nor a stray CR may reach the text",
        ("Първи ред от бележката.", "Втори ред от бележката."),
        bom=True,
        newline="\r\n",
        forbidden=("\r",),
    ),
)

_MIXED_SCRIPT_SAMPLES: tuple[_EncodingSample, ...] = (
    _EncodingSample(
        "Bulgarian and English in one line: neither script may be dropped or transliterated",
        ("Акме ООД confirmed the delivery on site.",),
    ),
    _EncodingSample(
        "Bulgarian and Greek in one line",
        ("Партньорът Παρτνερ ΑΕ подписа протокола.",),
    ),
    _EncodingSample(
        "Cyrillic and Latin look-alikes side by side: А (U+0410) and A (U+0041) are two scalars "
        "and must both survive as themselves",
        ("Позиция А и позиция A са различни.",),
    ),
    _EncodingSample(
        "Bulgarian prose with a Latin brand name and ASCII digits",
        ("Доставени са 12 броя Acme Trading SKU-4417.",),
    ),
    _EncodingSample(
        "Bulgarian prose with German umlauts and an eszett",
        ("Партньорът Müller Straße GmbH потвърди срока.",),
    ),
)

_NBSP_SAMPLES: tuple[_EncodingSample, ...] = (
    _EncodingSample(
        "NBSP thousands separator in plain text: it may render as a plain space, never vanish",
        (_NBSP_BG_LINE,),
        alternatives={_NBSP_BG_LINE: (_NBSP_BG_LINE.replace(_NBSP_BG, _NBSP_BG_ALT),)},
        forbidden=_NBSP_FORBIDDEN,
    ),
    _EncodingSample(
        "narrow no-break space (U+202F) inside a grouped amount",
        (_NBSP_EN_LINE,),
        alternatives={_NBSP_EN_LINE: (_NBSP_EN_LINE.replace(_NBSP_EN, _NBSP_EN_ALT),)},
        forbidden=_NBSP_FORBIDDEN,
    ),
    _EncodingSample(
        "NBSP amount inside a workbook cell",
        (_NBSP_BG,),
        fid_format="xlsx",
        alternatives=_NBSP_ALTERNATIVES,
        forbidden=_NBSP_FORBIDDEN,
    ),
    _EncodingSample(
        "NBSP amount inside a docx paragraph",
        (_NBSP_BG,),
        fid_format="docx",
        alternatives=_NBSP_ALTERNATIVES,
        forbidden=_NBSP_FORBIDDEN,
    ),
    _EncodingSample(
        "narrow NBSP amount inside an html paragraph",
        (_NBSP_EN,),
        fid_format="html",
        alternatives=_NBSP_ALTERNATIVES,
        forbidden=_NBSP_FORBIDDEN,
    ),
    _EncodingSample(
        "NBSP amount inside an rtf paragraph",
        (_NBSP_BG,),
        fid_format="rtf",
        alternatives=_NBSP_ALTERNATIVES,
        forbidden=_NBSP_FORBIDDEN,
    ),
)

_COMBINING_SAMPLES: tuple[_EncodingSample, ...] = (
    _EncodingSample(
        "Cyrillic и + combining breve (U+0306): the decomposed sequence is what the ORIGINAL says",
        (_DECOMPOSED_BG,),
    ),
    _EncodingSample(
        "Latin e + combining acute (U+0301) twice in one line",
        (_DECOMPOSED_LATIN,),
    ),
    _EncodingSample(
        "a combining acute over a Cyrillic vowel next to an English negation",
        (_DECOMPOSED_MIX,),
    ),
    _EncodingSample(
        "precomposed and decomposed forms of the same word in one line: both must survive as "
        "authored, so a silent normalization pass becomes visible",
        (_NORMALIZATION_PROBE,),
    ),
    _EncodingSample(
        "decomposed sequence inside a docx paragraph",
        (_DECOMPOSED_LATIN,),
        fid_format="docx",
    ),
)

_TYPOGRAPHIC_SAMPLES: tuple[_EncodingSample, ...] = (
    _EncodingSample(
        "Bulgarian typographic quotes and an em dash must not be flattened to ASCII",
        ("Клаузата „срок за плащане“ — пет работни дни.",),
    ),
    _EncodingSample(
        "an astral-plane emoji must survive as one scalar",
        ("Приложен документ \U0001f4c4 към писмото.",),
    ),
    _EncodingSample(
        "a horizontal ellipsis (U+2026) is one scalar, not three dots to invent",
        ("Списъкът продължава…",),
    ),
    _EncodingSample(
        "Turkish dotless i and dotted capital I are distinct scalars",
        ("Partner Iş Lojistik ve ısı hattı.",),
    ),
    _EncodingSample(
        "tab-separated cells with CRLF endings: the tab may collapse to one space, the cells may "
        "never merge into one token",
        ("Код\tСобственик\tСума", "C-1\tАкме ООД\t480,00 лв."),
        newline="\r\n",
        alternatives={
            "Код\tСобственик\tСума": ("Код Собственик Сума",),
            "C-1\tАкме ООД\t480,00 лв.": ("C-1 Акме ООД 480,00 лв.",),
        },
        forbidden=("\r", "КодСобственик", "C-1Акме"),
    ),
    _EncodingSample(
        "Arabic-Indic digits next to ASCII digits: two different scalar sets, both required",
        ("Количество ٤٥ срещу количество 45.",),
    ),
    _EncodingSample(
        "one line carrying an NBSP amount, a combining mark and typographic quotes together",
        (_NBSP_COMBINED,),
        alternatives={_NBSP_COMBINED: (_NBSP_COMBINED.replace(_NBSP_BG, _NBSP_BG_ALT),)},
        forbidden=_NBSP_FORBIDDEN,
    ),
)

_ENCODING_SAMPLES: tuple[_EncodingSample, ...] = (
    *_CP1251_SAMPLES,
    *_BOM_SAMPLES,
    *_MIXED_SCRIPT_SAMPLES,
    *_NBSP_SAMPLES,
    *_COMBINING_SAMPLES,
    *_TYPOGRAPHIC_SAMPLES,
)


def build_encoding_cases() -> tuple[FidCase, ...]:
    """Build the encoding / Unicode half of the battery (`fid-091` .. `fid-124`).

    Returns:
        The thirty-four encoding cases in `case_id` order.
    """
    cases: list[FidCase] = []
    for index, sample in enumerate(_ENCODING_SAMPLES, start=91):
        payload, content_type = _payload(sample)
        cases.append(
            fid_case(
                f"fid-{index:03d}",
                f"encoding — {sample.origin}",
                sample.fid_format,
                content_type,
                payload,
                _expectation(sample),
            )
        )
    return tuple(cases)
