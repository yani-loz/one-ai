# TC-IM-D02 — Billion-laughs / XXE in an `application/xml` attachment is inert

| Field | Value |
|---|---|
| **ID** | TC-IM-D02 · **Suite** D (Attachments) · **Type** Adversarial · **Mode** pure |
| **Result** | 📋 **Pass** (defense held) · **Tag** 📋 CONFIRMS-DOCUMENTED (CA-CONN-04) · **Severity if fail** High (XXE file read / billion-laughs DoS) · **Status** Executed |
| **Harness** | `harness/attachment_suite.py` (`tc_d02_billion_laughs_xxe`) |

## Objective
Prove that an XML attachment carrying a billion-laughs entity bomb and an XXE external-entity file-read
payload causes **no entity expansion and no file read** — even though `application/xml` IS decoded.

## Break hypothesis
**The teeth of this suite.** Unlike PDF/zip, `application/xml` *is* in `_TEXT_EXACT`, so `extract_text`
**does** process it. If `_decode_text` instantiated any XML parser (stdlib `xml.*`/`expat`, `lxml`), the
billion-laughs entities would expand exponentially (memory DoS) and a `SYSTEM "file:///etc/passwd"`
external entity would inject the file's contents into `extracted_text` (XXE → cross-system data
disclosure). The plain reading of the code: `_decode_text` is a **raw `payload.decode("utf-8")`** — no
parser — so the markup is treated as opaque text and both payloads are inert.

## Steps (the harness)
1. Feed a classic billion-laughs payload (`&lol9;` nested entities) as `application/xml`.
   Assert: `&lol9;` and `<!ENTITY lol1` appear **verbatim** in the output (no expansion) **and** output
   length == input length (313 == 313 — a real XML parser would expand the entity; the classic
   billion-laughs ladder reaches ~10⁹ chars, and even this trimmed payload would balloon).
2. Feed an XXE payload (`<!ENTITY xxe SYSTEM "file:///etc/passwd">` … `&xxe;`) as `application/xml`.
   Assert: `&xxe;` appears verbatim **and** `root:` is **absent** (no `/etc/passwd` read).
3. Source check: `attachment_extractor.py` references no `xml`/`lxml`/`etree`/`expat`.

## Expected
Entity references survive verbatim, no length blow-up, no `/etc/passwd` contents in output, no XML
parser reachable.

## Execution result (2026-06-09)
```
  [PASS] d02_billion_laughs_entities_verbatim :: '&lol9;' present verbatim & not expanded (out_len=313)
  [PASS] d02_no_exponential_blowup :: in=313 out=313 (would be ~10^9 if expanded)
  [PASS] d02_xxe_no_file_read :: '&xxe;' verbatim, 'root:' absent -> no external-entity file read (out_len=99)
  [PASS] d02_extractor_imports_no_xml_parser :: attachment_extractor.py references no xml/lxml/etree/expat
```
**Verdict — 📋 Pass (INERT).** The discriminating checks are real teeth: (1) the literal entity tokens
survive, proving **no expansion**; (2) output length == input length, so the billion-laughs bomb did not
amplify; (3) `root:` is absent, proving **no external-entity file read** occurred. The XML is stored as
opaque text via `bytes.decode` — exactly the safe behavior, and these checks would have caught a real
parser if one were on the path.

**Tag — 📋 CONFIRMS-DOCUMENTED (CA-CONN-04).** The highest-value latent in this suite. The moment a
real XML/structured extractor is added under CA-CONN-04, it **MUST** disable DTD/external-entity
resolution (use `defusedxml` or `resolve_entities=False`) and cap entity expansion — otherwise this
exact payload becomes a live XXE + DoS. Inert today **only** because `application/xml` is byte-decoded,
never parsed. Flag as a required hardening on the CA-CONN-04 extractor.
