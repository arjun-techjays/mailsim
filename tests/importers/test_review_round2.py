"""Second review round: keyed identifier hashing and the independent digit-id scan (B-1),
attachment scanning coverage and parser limits (S-1, S-2), real organisations (D-3), dates
that follow the tense of the mail (D-4), voice and recipient coherence (D-7), place pseudonyms
(D-8), document branding, kinds and titles (D-2), the scripted CEO triggers and the
per-category curation word lists. Every name, firm and number here is made up."""

from __future__ import annotations

import email
import io
import json
import re
import zipfile
import zlib
from datetime import UTC, date, datetime
from email import policy

import pytest

from mailsim.domain.models import Address
from mailsim.importers import attachments as A
from mailsim.importers import curation as C
from mailsim.importers import dates as D
from mailsim.importers import documents as DOC
from mailsim.importers import enron, pii
from mailsim.importers import leadership as L
from mailsim.importers import names as N
from mailsim.importers.pii import Scanner, digit_ids, org_mentions
from mailsim.importers.pseudonymize import Harvester, Pseudonymizer, Renderer
from mailsim.mime.build import build_mime
from mailsim.mime.parse import ParsedAttachment

KEY = b"0" * 64


def _pseudo(seed: int = 5, key: bytes = KEY) -> Pseudonymizer:
    h = Harvester()
    h.feed("From: Wendell Quarrick <wendell.quarrick@enron.com>\n"
           + "the plant site and the trader pool were fine today and we will meet again\n" * 4)  # fmt: skip
    return Pseudonymizer(h.finish(), seed, key)


def _mail(text: str, attachments: list[ParsedAttachment] | None = None, *,
          message_id: str = "<a1@corvane.example.test>") -> bytes:  # fmt: skip
    return build_mime(
        message_id=message_id,
        from_=Address(email="rohan.desai@corvane.example.test", name="Rohan Desai"),
        to=[Address(email="owen.castillo@corvane.example.test", name="Owen Castillo")],
        cc=[], subject="Numbers", date=datetime(2026, 10, 9, 10, tzinfo=UTC), text=text,
        attachments=attachments or [],
    )  # fmt: skip


# ------------------------------------------------------------------- identifiers (B-1)

RAW_IDS = (
    "The deals are Gridbook#'s 464394,464390 and 257561; Notice 2001043 is out; see #549048 "
    "and # 706294, deal 48213 and run 8831234. We paid $150000 for 1234567.5 units; "
    "ref 20011014 and the 20011014 file."
)


def test_identifiers_are_hashed_with_the_key_and_listed():
    p = _pseudo()
    out = p.apply(RAW_IDS).text
    for real in ("464394", "464390", "257561", "2001043", "549048", "706294", "48213", "8831234"):
        assert real not in out, real
    assert "$150000" in out and "1234567.5" in out  # money, decimals
    assert out.endswith("the 20011014 file.")  # an unlabelled compact date stays
    assert digit_ids(out, frozenset(p.emitted)) == []  # every other 6+ run is a listed hash
    assert digit_ids(out)  # ... which the scanner flags without the manifest
    assert _pseudo().apply(RAW_IDS).text == out  # deterministic for one key
    assert _pseudo(key=b"1" * 64).apply(RAW_IDS).text != out  # another key, other ids
    assert _pseudo(seed=99).apply(RAW_IDS).text == out  # the public seed plays no part


def test_import_records_list_their_hashed_ids(shards):
    records, _header, _reg = enron.import_enron(shards, enron.ImportConfig(limit=300, id_key=KEY))
    assert any(rec["ids"] for rec in records)
    for rec in records:
        blob = json.dumps(rec["segments"])
        assert digit_ids(blob, frozenset(rec["ids"])) == []
        assert rec["ids"] == sorted(set(rec["ids"])) and all(len(i) >= 6 for i in rec["ids"])


def test_id_key_file_is_created_once_and_private(tmp_path):
    path = tmp_path / "var" / "id.key"
    key = enron.load_or_create_key(path)
    assert len(key) == 64 and path.stat().st_mode & 0o777 == 0o600
    assert enron.load_or_create_key(path) == key
    path.write_text("short\n")
    with pytest.raises(ValueError, match="too short"):
        enron.load_or_create_key(path)


def test_digit_rule_is_independent_of_the_scrubber(tmp_path):
    s = Scanner()
    text = "Notice 2001043 and deal 903029 cost $150000 (1234567.5) on 20261014 at USD 450000"
    assert [f.match for f in s.scan_text(text) if f.kind == "digit-id"] == ["2001043", "903029"]
    assert [f.match for f in s.scan_text(text, allowed=frozenset({"903029"}))
            if f.kind == "digit-id"] == ["2001043"]  # fmt: skip
    raw = _mail("No ids here.\n", message_id="<12345678901234@corvane.example.test>")
    assert [f for f in s.scan_message(raw) if f.kind == "digit-id"] == []  # ids, boundaries
    pack = tmp_path / "pack"
    (pack / "fixtures").mkdir(parents=True)
    (pack / "pack.json").write_text(json.dumps({"hashed_ids": ["903029"]}))
    (pack / "scenario.yaml").write_text("max_virtual_duration_seconds: 604800\n")
    (pack / "fixtures" / "001.eml").write_bytes(_mail("deal 903029 and notice 2001043\n"))
    found = [(f.kind, f.match) for f in s.scan_paths([pack])]
    assert found == [("digit-id", "2001043")]


# ------------------------------------------------------- attachment scanning (S-1, S-2)


def _zip(members: dict[str, bytes], method: int = zipfile.ZIP_DEFLATED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", method) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_pdf_info_xmp_and_hex_strings_are_scanned():
    names = ["Marta Pellowe", "Livia Tazewell", "Osgood Brannick"]
    s = Scanner(names)
    data = A.pdf_bytes([[A.Line("p", "Quarterly plan")]], title="Plan", author="Marta Pellowe",
                       created="20261009120000")  # fmt: skip
    assert "Marta Pellowe" in A.pdf_text(data)
    xmp = b"<?xpacket begin?><x:xmpmeta><dc:creator>Livia Tazewell</dc:creator></x:xmpmeta>"
    crafted = (b"%PDF-1.4\n1 0 obj\n<< /Type /Metadata /Subtype /XML /Length 80 >>\nstream\n"
               + xmp + b"\nendstream\nendobj\n2 0 obj\n<< /Author <4F73676F6F64204272616E6E69636B>"
               b" >>\nendobj\n")  # fmt: skip
    text = A.extract_text(crafted, "x.pdf")
    assert "Livia Tazewell" in text and "Osgood Brannick" in text
    kinds = [f.kind for f in s.scan_text(text)]
    assert kinds.count("real-name") == 2


def test_office_packages_are_scanned_part_by_part():
    docx = _zip({
        "word/document.xml": b'<w:document><w:p><w:r><w:t>Call Marta Pellowe</w:t></w:r></w:p>'
                             b"</w:document>",
        "word/comments.xml": b'<w:comments><w:comment w:author="Livia Tazewell"/></w:comments>',
        "docProps/core.xml": b"<cp:coreProperties><dc:creator>Osgood Brannick</dc:creator>"
                             b"</cp:coreProperties>",
        "xl/drawings/drawing1.xml": b"<xdr:wsDr><a:t>PG&amp;E exposure</a:t></xdr:wsDr>",
    })  # fmt: skip
    text = A.extract_text(docx, "memo.docx")
    for needle in ("Marta Pellowe", "Livia Tazewell", "Osgood Brannick", "PG&E exposure"):
        assert needle in text
    att = ParsedAttachment("memo.docx", "application/vnd.openxmlformats-officedocument."
                           "wordprocessingml.document", docx, False, None)  # fmt: skip
    found = Scanner(["Marta Pellowe", "Livia Tazewell", "Osgood Brannick"]).scan_message(
        _mail("See the memo.\n", [att])
    )
    kinds = sorted(f.kind for f in found)
    assert kinds.count("real-name") == 3 and "real-org" in kinds


def test_unreadable_and_oversized_attachments_are_findings(monkeypatch, tmp_path):
    s = Scanner()
    garbage = ParsedAttachment("scan.pdf", "application/pdf", b"%PDF-1.4 \x00\x01\x02", False, None)
    assert [f.kind for f in s.scan_message(_mail("Scan attached.\n", [garbage]))] == [
        "unreadable-attachment"
    ]
    bomb = _zip({"xl/sheet.xml": b"0" * 2_000_000})
    with pytest.raises(A.UnsafeAttachment, match="exceeds"):
        A.extract_text(bomb, "b.xlsx")
    att = ParsedAttachment("b.xlsx", "application/zip", bomb, False, None)
    assert "unsafe-attachment" in [f.kind for f in s.scan_message(_mail("x\n", [att]))]
    monkeypatch.setattr(A, "MAX_INFLATE", 100)
    stored = _zip({"word/document.xml": b"<w:t>" + b"a" * 500 + b"</w:t>"}, zipfile.ZIP_STORED)
    with pytest.raises(A.UnsafeAttachment):
        A.zip_text(stored)
    flate = (b"%PDF-1.4\n1 0 obj\n<< /Filter /FlateDecode >>\nstream\n"
             + zlib.compress(b"(x) Tj " * 200) + b"\nendstream\nendobj\n")  # fmt: skip
    with pytest.raises(A.UnsafeAttachment, match="inflates"):
        A.pdf_text(flate)
    monkeypatch.setattr(A, "MAX_INFLATE", 8 * 1024 * 1024)
    monkeypatch.setattr(A, "MAX_TOTAL", 50)
    with pytest.raises(A.UnsafeAttachment):
        A.pdf_text(flate)
    with pytest.raises(A.UnsafeAttachment):
        A.zip_text(_zip({"a.xml": b"<t>" + b"b" * 200 + b"</t>"}, zipfile.ZIP_STORED))
    monkeypatch.setattr(A, "MAX_ATTACHMENT", 10)
    for call in (lambda: A.extract_text(b"x" * 11), lambda: A.pdf_text(b"%PDF" + b"x" * 11),
                 lambda: A.zip_text(b"PK" + b"x" * 11)):  # fmt: skip
        with pytest.raises(A.UnsafeAttachment):
            call()
    monkeypatch.setattr(pii, "MAX_ATTACHMENT", 10)
    big = tmp_path / "big.txt"
    big.write_text("x" * 20)
    assert [f.kind for f in s.scan_paths([big])] == ["unsafe-attachment"]
    many = _zip({f"m{i}.xml": b"<t/>" for i in range(5)})
    monkeypatch.setattr(A, "MAX_MEMBERS", 3)
    monkeypatch.setattr(A, "MAX_ATTACHMENT", 16 * 1024 * 1024)
    with pytest.raises(A.UnsafeAttachment, match="too many"):
        A.zip_text(many)
    (tmp_path / "p.pdf").write_bytes(flate)
    monkeypatch.setattr(pii, "MAX_ATTACHMENT", 16 * 1024 * 1024)
    assert "unsafe-attachment" in [f.kind for f in s.scan_paths([tmp_path / "p.pdf"])]


def test_pdf_extraction_is_a_single_pass():
    # no backtracking blow-up on hostile input: unterminated streams, strings and hex
    hostile = b"%PDF-1.4\n" + b"<< /A 1 >> stream\n" * 5000 + b"(" * 20000 + b"<" * 20000
    assert isinstance(A.pdf_text(hostile), str)
    utf16 = b"%PDF-1.4\n1 0 obj\n<< /Title (\xfe\xff\x00H\x00i) /T <48 49 4> >>\nendobj\n"
    assert {"Hi", "HI@"} <= set(A.pdf_text(utf16).split("\n"))
    endless = b"%PDF-1.4\n1 0 obj\n<< >>\nstream\n(never ends"
    assert A.pdf_text(endless) == ""
    assert A.extract_text(b"PK\x03\x04broken", "x.docx") == ""


# --------------------------------------------------------------- organisations (D-3)


@pytest.mark.parametrize(
    "text",
    [
        "The resolution The Bakersfield Californian provided to us",
        "We need to protect the flows to Citizens on the line.",
        "Let Sempra know that the gas will not do.",
        "the custody agreement with Chase Manhatten Bank",
        "Michigan Gas Utilities - $5 MM",
        "PSEG - $2 MM",
        "Philadelphia Gas Works - $1MM",
        "Texas General Land Office - $500k",
        "membership in the Western Coal Council",
        "an article in Global Finance",
        "will deal with el paso flows",
        "the Williams deal at 215.00",
        "purchase from New Power",
        "the Washington Post",
    ],
)
def test_real_organisations_are_flagged(text):
    assert org_mentions(text)
    assert "real-org" in {f.kind for f in Scanner().scan_text(text)}


@pytest.mark.parametrize(
    "text",
    [
        "CAISO, NYISO, PJM, ICE, EEI, FERC, ERCOT and NYMEX prices; WTI at Henry Hub",
        "Corvane Energy Inc. Treasury Department",
        "Brightmoor Capital LLC and Westerholt Power Inc.",
        "Brightwater Audit LLP, Harbor Bank, Northstar Partners, Marlow & Pike LLP",
        "The Credit Group",
        "Our Company",
        "Halden Escrow Services Ltd at Coastline Commercial Bank",
        "Corvane Federal Credit Union",
    ],
)
def test_public_and_fictional_organisations_pass(text):
    assert org_mentions(text) == []


def test_public_orgs_are_operators_and_regulators_only():
    for name in ("Chase", "Sempra", "PG&E", "Dynegy", "Williams", "Goldman", "Shell", "Microsoft"):
        assert name not in N.PUBLIC_ORGS
    assert "Dynegy" not in N.NEVER_PERSON_ORGS and "Dynegy" in N.ENTITY_MAP
    assert {"CAISO", "PJM", "FERC", "WTI", "ICE", "EEI", "NYISO"} <= N.PUBLIC_ORGS


def test_short_entity_acronyms_are_mapped_and_scanned():
    p = _pseudo()
    out = p.apply("EA and TGT report to Enron Americas; Texas Gas Transmission and TW; EAST "
                  "and TWh stay.").text  # fmt: skip
    assert out == ("CVA and BGT report to Corvane Americas; Bayline Gas Transmission and WL; "
                   "EAST and TWh stay.")  # fmt: skip
    found = {f.match for f in Scanner().scan_text("EA, TGT and TW") if f.kind == "enron-acronym"}
    assert found == {"EA", "TGT", "TW"}


# ------------------------------------------------------------------------ dates (D-4)

LIMIT = date(2026, 10, 15)


@pytest.mark.parametrize(
    ("text", "sent", "expected"),
    [
        # future tense: the meeting lands after the message
        ("Please plan to attend a meeting\n\n   Wednesday, May 16, 2001\n   at 3pm",
         date(2026, 10, 9), "Wednesday, October 14, 2026"),
        # year-less date in a record without a full date, future tense: just after the message
        ("Your scheduled review time with Yara is 4:00 p.m., January 2nd. Thanks.",
         date(2026, 10, 12), "October 15th"),
        # past tense: just before it
        ("We had a great call on 6/30 with them.", date(2026, 10, 12), "on 10/10"),
        # a month-only mention moves with the record
        ("it will probably be early July before we get their position.", date(2026, 10, 6),
         "early October"),
    ],
)  # fmt: skip
def test_dates_follow_the_tense_of_the_mail(text, sent, expected):
    shift = D.plan_record([(text, sent)], sent, LIMIT)
    assert shift is not None
    out = D.apply(text, shift)
    assert expected in out and D.date_conflicts(out, sent) == []


@pytest.mark.parametrize(
    ("text", "sent"),
    [
        ("We are only a couple of weeks from June 1. Please advise.", date(2026, 10, 9)),
        ("We had a call on 6/30 and will meet again on 7/9.", date(2026, 10, 12)),
        ("The interview will happen Friday, May 11, 2001. Print the documents.",
         date(2026, 10, 12)),  # the next Friday is after the clock start
        ("The review was held on Friday, May 4, 2001. The plan will be signed on Tuesday, "
         "May 1, 2001.", date(2026, 10, 9)),
    ],
    ids=["no-tense", "mixed-tense", "no-room-before-start", "contradictory"],
)  # fmt: skip
def test_undatable_records_are_dropped(text, sent):
    assert D.plan_record([(text, sent)], sent, LIMIT) is None


def test_mentions_and_conflicts_read_shifted_text():
    sent = date(2026, 10, 9)
    text = ("The deadline is October 2, 2026. We met on 10/12. Please attend on Friday, "
            "October 16. Budget for late November.")  # fmt: skip
    found = [(a.isoformat(), b.isoformat()) for a, b, _s, _e in D.mentions(text, sent)]
    assert found == [("2026-10-02", "2026-10-02"), ("2026-10-12", "2026-10-12"),
                     ("2026-10-16", "2026-10-16"), ("2026-11-01", "2026-11-30")]  # fmt: skip
    conflicts = D.date_conflicts(text, sent)
    assert len(conflicts) == 2 and "October 2, 2026 (future" in conflicts[0]
    assert "10/12 (past" in conflicts[1]
    assert D.tense_at("nothing here 10/12", 13, 18) is None
    assert D.tense_at("on 10/12 we will meet", 3, 8) == "future"
    assert D.tense_at("on 10/12 the deal closed", 3, 8) == "past"
    assert D.plan_record([("no dates at all", sent)], sent, LIMIT) == D.Shift()


# ------------------------------------------------------------- voice and roles (D-7)


@pytest.mark.parametrize(
    ("group", "text", "kw", "reason"),
    [
        ("external", "The deal tickets are entered and our traders will confirm them.",
         {"sender_id": "bd_partner"}, "insider-voice"),
        ("vendor", "Our traders and the Corvane Energy Inc. Treasury Department need the files.",
         {"sender_id": "auditor"}, "insider-voice"),
        ("vendor", "Here is a breakdown of the expenditures to develop the coal site.",
         {"sender_id": "it_vendor"}, "out-of-domain"),
        ("vendor", "Is the application still in use? Please check the version.",
         {"sender_id": "it_vendor", "to_groups": ("vendor",)}, "outsiders-only"),
        ("exec", "After speaking to other lawyers, I hope I'm speaking to the right person.",
         {}, "stranger-voice"),
        ("director", "Please match the following deals for March.",
         {"parties": ("cbo", "ceo")}, "back-office-for-ceo"),
        ("exec", "These two deals were input as not to be confirmed.",
         {"parties": ("ceo", "director_sales")}, "back-office-for-ceo"),
        ("assistant", "Attached is the revised Precedent Agreement with the changes we discussed.",
         {}, "negotiation-by-assistant-or-board"),
        ("external", "Can you live with this version of the agreement and the TSA revisions?",
         {"sender_id": "bd_partner", "to_groups": ("assistant",)},
         "negotiation-by-assistant-or-board"),
    ],
)  # fmt: skip
def test_voice_and_recipient_rules(group, text, kw, reason):
    assert C.role_reason(group, text, **kw) == reason


@pytest.mark.parametrize(
    ("group", "text", "kw"),
    [
        ("external", "Please confirm the letter of credit amount for the facility.",
         {"sender_id": "banker", "to_groups": ("exec",)}),
        ("vendor", "The audit fieldwork starts on Monday.", {"sender_id": "auditor"}),
        ("exec", "The board approved the plan.", {"parties": ("cfo", "ceo")}),
    ],
)  # fmt: skip
def test_coherent_mail_passes(group, text, kw):
    assert C.role_reason(group, text, **kw) is None


# ------------------------------------------------------------------ places (D-8)


@pytest.mark.parametrize(
    "text",
    [
        "Sorry - Bill just changed this deal from Wheatland to NP-15.",
        "Gas for the Zorbo Power Pool today.",
        "Lunch with the Quixley Trader team.",
        "We met at the Fontainbleu last night.",
        "Do we have a site rep for both Kelso and Marsing? The units at Marsing are down.",
    ],
)
def test_place_and_asset_names_get_place_pseudonyms(text):
    p = _pseudo()
    out = p.apply(text).text
    assert "{{l:" in out
    for real in ("Wheatland", "Zorbo", "Quixley", "Fontainbleu", "Kelso", "Marsing"):
        assert real not in out
    rendered = Renderer(1).render(out)
    assert any(place in rendered for place in N.FAKE_PLACES)


def test_pseudonym_pools_are_disjoint():
    people = set(N.FAKE_FIRST) | set(N.FAKE_LAST) | N.PERSONA_FIRST
    places, orgs = set(N.FAKE_PLACES), set(N.FAKE_ORG_NAMES)
    assert not (places & people) and not (orgs & people) and not (places & orgs)
    assert len(places) == len(N.FAKE_PLACES)
    r = Renderer(1)
    r.new_scope()
    assert len({r.identity(f"l:{i}").name for i in range(len(N.FAKE_PLACES))}) == len(places)


def test_unplaceable_tokens_stay():
    p = _pseudo()
    for text in ("We met at the Board meeting.", "Power from the West is cheap.",
                 "The site visit was at Chicago."):  # fmt: skip
        assert "{{l:" not in p.apply(text).text


# ------------------------------------------------------------------ documents (D-2)


def _ctx(**kw):
    base = dict(seed=7, key="k1", subject="Q3 Budget Review", body="Attached is the memo.",
                sender_name="Fiona Reyes", sender_role="Relationship Manager, Harbor Bank",
                recipient_name="Rohan Desai", when=datetime(2026, 10, 9, 10, tzinfo=UTC),
                org="Harbor Bank", internal=False)  # fmt: skip
    base.update(kw)
    return DOC.Context(**base)


def test_documents_carry_the_senders_organisation():
    memo = A.pdf_text(DOC.build("memo", _ctx()).data)
    assert "Harbor Bank  |  Memorandum" in memo and "Internal memorandum" not in memo
    assert "Corvane Energy -" not in memo and "Harbor Bank - Q3 Budget Review" in memo
    internal = A.pdf_text(DOC.build("memo", _ctx(org=N.COMPANY_LONG, internal=True)).data)
    assert "Corvane Energy  |  Internal memorandum" in internal
    invoice = A.pdf_text(DOC.build("invoice", _ctx()).data)
    assert "Harbor Bank  |  Accounts Receivable" in invoice and N.FAKE_ADDRESS in invoice
    schedule = A.pdf_text(DOC.build("schedule", _ctx()).data)
    assert "offices of Harbor Bank" in schedule and N.FAKE_STREET not in schedule
    roster = DOC.build("roster", _ctx(roster=(("ceo", "Helena Marsh", "Chief Executive"),)))
    assert "Helena Marsh" not in A.pdf_text(roster.data)  # an outsider never sends the list
    staff = DOC.build("roster", _ctx(org=N.COMPANY_LONG, internal=True,
                                     roster=(("ceo", "Helena Marsh", "Chief Executive"),)))  # fmt: skip
    assert "Helena Marsh" in A.pdf_text(staff.data)
    deck = DOC.build("presentation", _ctx(subject="Pricing Proposal"), filename_title="Pricing")
    assert deck.filename == "Pricing-2026-10-09.pdf" and "Market view" in A.pdf_text(deck.data)
    contract = A.pdf_text(DOC.build("contract", _ctx()).data)
    assert '("Counterparty")' in contract and "and Harbor Bank (" in contract


@pytest.mark.parametrize(
    ("text", "focus", "kind"),
    [
        ("Here are the numbers.", "Credit Worksheet", "spreadsheet"),
        ("Rough cut at slides for the meeting.", "", "presentation"),
        ("Attached please find the interview packet for the person.", "", "memo"),
        ("Attached is the agenda. The contract is under review.",
         "Attached is the agenda", "schedule"),
        ("Attached is a revised hotlist of names.", "", "credit_watch"),
    ],
)  # fmt: skip
def test_document_kind_comes_from_the_announced_file(text, focus, kind):
    assert DOC.kind_of(text, focus=focus) == kind


def test_document_titles_are_names_not_fragments():
    assert DOC.title_like("Precedent Agreement") and DOC.title_like("RE: Q3 Budget Review")
    assert not DOC.title_like("E-mail has been a bit problematic here")
    assert not DOC.title_like("Can you take a look?")
    assert DOC.stub_title("<<PrecedentnewJan_Corvane.doc>>") is None
    assert DOC.stub_title(" - storage proxies.xls") == "Storage Proxies"
    body = "Julia, attached is the revised Precedent Agreement with the changes we discussed."
    assert DOC.doc_title("contract", "x", body, subject_is_title=False) == "Precedent Agreement"
    body2 = "E-mail is slow here. I attach a copy of the sale and purchase contract."
    assert DOC.doc_title("contract", "E-mail is slow here", body2, subject_is_title=False) == (
        "Sale and Purchase Contract"
    )
    assert DOC.doc_title("report", "We met today", "Some notes.", subject_is_title=True) == (
        "Summary Report"
    )
    assert DOC.doc_title("contract", "Any", "Attached is a rough cut at slides we use.",
                         subject_is_title=False) == "Slides"  # fmt: skip
    assert DOC.doc_title("memo", "x", "y", subject_is_title=False, stub="Stub Name") == "Stub Name"


def test_attachment_claims_and_title_lines():
    body = "Rohan - Credit Worksheet and Threshold table.\n\nCredit Worksheet\n\nThreshold Table\n\nFiona"
    clean, titles = DOC.trailing_titles(body)
    assert titles == ["Credit Worksheet", "Threshold Table"] and "Worksheet\n" not in clean
    heading = "Agenda\n\n1. Opening remarks and introductions for the whole team\n\nOwen"
    assert DOC.trailing_titles(heading) == (heading, [])
    for claim in ("Attached: Credit Worksheet.", "the latest issue in .pdf format",
                  "Print all three documents for the meeting", "Please see the attached.",
                  "<<plan.doc>>", "Credit Worksheet\n\nThreshold Table"):  # fmt: skip
        assert DOC.claims_attachment("Hello.\n\n" + claim), claim
    assert not DOC.claims_attachment("The attached is a link: see the url.")
    assert not DOC.claims_attachment("Let's meet on Friday.")
    assert DOC.claim_sentence("Hi. Attached is the plan for May. Thanks.") == (
        "Attached is the plan for May."
    )
    assert DOC.claim_sentence("No claim.") == ""


def test_title_lines_become_attachments_in_generated_mail():
    from tests.importers.test_generator_units import HEADER, rec, seg

    body = ("{{q:3:f}} - Credit Worksheet and Threshold table for the plant budget.\n\n"
            "Credit Worksheet\n\nThreshold Table")  # fmt: skip
    gen = L.Generator(HEADER, [rec("r1", seg(body=body), topic="ops")],
                      L.GenerateOptions(seed=1, roles=["coo"], volume={"coo": 1}))  # fmt: skip
    sc = gen.generate_role("coo")
    raw = next(v for k, v in sc.files.items() if k.endswith(".eml"))
    m = email.message_from_bytes(raw, policy=policy.default)
    names = [a.get_filename() for a in m.iter_attachments()]
    assert names == ["Credit-Worksheet-2026-10-06.xlsx", "Threshold-Table-2026-10-06.xlsx"] or (
        len(names) == 2 and all(n.endswith(".xlsx") for n in names)
    )
    text = m.get_body(("plain",)).get_content()
    assert "Attached: Credit Worksheet, Threshold Table." in text
    assert DOC.claims_attachment(text)


# ------------------------------------------------------------ triggers and word lists


def test_ceo_triggers_are_whole_payment_words():
    def fires(body: str) -> bool:
        return any(t in body.lower() for t in L.CEO_TRIGGERS)

    assert not any(w in t for t in L.CEO_TRIGGERS for w in ("request", "release"))
    assert all(t.startswith(" ") for t in L.CEO_TRIGGERS)
    assert fires("I will wire the funds today.") and fires("Is the payment done?\n")
    assert fires("The deposit, please.") and fires("Funds in escrow.")
    assert not fires("Please release the request.") and not fires("The wireless network")
    assert not fires("The deposition is set.") and not fires("Transfers are fine")


@pytest.mark.parametrize(
    ("rx", "words", "samples"),
    [
        (C.SENSITIVE, C.SENSITIVE_WORDS, {
            "credentials": "my password is", "health": "the hospital called",
            "private_life": "my wife and I", "employment": "the severance terms",
            "legal_trouble": "an insider trading case", "company_crisis": "the chapter 11 filing",
            "politics_news": "the governor said", "personal_finance": "my investment account"}),
        (C.AUTOMATED, C.AUTOMATED_WORDS, {
            "notification": "This is an automated message", "mass_mail": "Click here to unsubscribe",
            "announcement": "We are pleased to announce it", "event": "Join the webcast",
            "helpdesk": "Contact the help desk"}),
        (C.OFF_ROLE, C.OFF_ROLE_WORDS, {
            "career": "your boss said", "home": "the garage door", "family": "my cousin is",
            "school": "the semester starts", "media_sports": "the football game",
            "personal_money": "running out of cash", "social": "the party at my house"}),
        (C.SPAM, C.SPAM_WORDS, {
            "promotion": "save 20% now", "finance_spam": "lower mortgage rates",
            "web_junk": "free download here"}),
    ],
    ids=["sensitive", "automated", "off-role", "spam"],
)  # fmt: skip
def test_every_curation_category_has_a_rule(rx, words, samples):
    assert set(samples) == set(words)
    for cat, text in samples.items():
        assert C.category(rx, text) == cat, cat
    assert C.category(rx, "The plant budget review is on Friday.") is None


def test_spam_shouting_and_love_signoff():
    assert C.spam_reason("WIN BIG!!!") == "shouting" and C.spam_reason("feel free") is None
    assert C.role_reason("exec", "See you soon.\nLove,\nMom") == "off-role"
    assert C.role_reason("director", "The PRC meets on Friday.") == "off-role"


def test_record_eligibility_is_cached(monkeypatch):
    from tests.importers.test_generator_units import HEADER, rec, seg

    calls = []
    real = L.Generator._check_record

    def counting(self, record):
        calls.append(record["id"])
        return real(self, record)

    monkeypatch.setattr(L.Generator, "_check_record", counting)
    gen = L.Generator(HEADER, [rec("r1", seg(), topic="ops"), rec("r2", seg(body="x"))],
                      L.GenerateOptions(seed=1, roles=["coo", "director_engineering"]))  # fmt: skip
    gen.generate_role("coo")
    gen.generate_role("director_engineering")
    assert sorted(calls) == ["r1", "r2"]


def test_persona_organisations():
    assert L.BY_ID["cfo"].org == N.COMPANY_LONG and L.BY_ID["cfo"].internal
    assert L.BY_ID["banker"].org == "Harbor Bank" and not L.BY_ID["banker"].internal
    assert L.BY_ID["auditor"].org == "Brightwater Audit LLP"
    assert L.BY_ID["board_chair"].org == "Board of Directors"
    assert L.BY_ID["industry_news"].org == "Energy Markets Daily" and L.BY_ID["friend"].org == ""
    assert all(p[0] in L.BY_ID and L.BY_ID[p[0]].internal for p in L.STAFF)
    assert re.fullmatch(r"[a-z_]+", L.STAFF[0][0])


def test_pdf_and_zip_extraction_edge_forms():
    pdf = (b"%PDF-1.4\n1 0 obj\n<< /Length 30 >>\nstream\r\n(Line\\none \\(x\\) \\101) Tj\r\n"
           b"endstream\nendobj\n2 0 obj\n<< /Length 5 >>\nstream\n(Two) Tj\nendstream\nendobj\n")  # fmt: skip
    text = A.pdf_text(pdf)
    assert "Line\none (x) A" in text and "Two" in text
    archive = _zip({"notes.txt": b"plain notes", "image.png": b"\x89PNG", "dir/": b""})
    assert A.zip_text(archive) == "plain notes"


def test_document_names_people_sheets_and_long_excerpts():
    ctx = _ctx(subject="Trading Track Rotations", body="Attached is the list of rotations.",
               org=N.COMPANY_LONG, internal=True)  # fmt: skip
    doc = DOC.build("spreadsheet", ctx)
    sheet = A.xlsx_text(doc.data)
    assert "Current assignment" in sheet and "Counterparty" not in sheet
    assert any(f in sheet for f in N.FAKE_FIRST)
    long = DOC.build("memo", _ctx(body="word " * 400))
    assert "..." in A.pdf_text(long.data)
    assert DOC.trailing_titles("Hi.\n\nCredit Worksheet\n\nMore text follows here for the team "
                               "to read today.\n\nOwen") == (
        "Hi.\n\nCredit Worksheet\n\nMore text follows here for the team to read today.\n\nOwen", [])
    assert DOC.announced_name("Attached is the Gas and Power Master Agreement and Schedule.") == (
        "Gas and Power Master Agreement and Schedule"
    )
    assert DOC.announced_name("Attached is the draft of our.") is None
    assert DOC.announced_name("Attached is the file.") is None
    assert DOC.announced_name("Attached are numbers only.") is None
    assert DOC.announced_name("No claim here.") is None
    assert DOC.announced_name("Attached is my latest and final report you asked for.") == "Report"


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("{{q:1:f}} and Rose,\n\nThank you for the presentation on the plan yesterday.",
         "residual-name"),
        ("I THOUGHT YOU SAID THE FED WOULD LET YOU KNOW BEFORE THEY RAISED RATES AGAIN TODAY.",
         "shouting"),
        ("is outstanding (I think anyway) - can we hire him for the team?", "starts-mid-sentence"),
        ("Please see the revised schedule below.", "dangling-reference"),
        ("Sorry I did not get back to you earlier today, but here's my email address.",
         "dangling-reference"),
        ("Hi,\n\nDG\n\nI am in Chicago today and back on Monday for the meeting.", "damaged"),
    ],
)  # fmt: skip
def test_junk_rules_of_the_second_round(body, reason):
    assert C.junk_reason([{"body": body, "subject": ""}]) == reason


def test_rendered_text_checks_and_more_voice_rules():
    assert C.rewrite_greeting("Thea and Rose,\n\nThanks", "Rohan") == "Rohan,\n\nThanks"
    assert C.misplaced_names("not Long Term Caleb, and the Silas Plant")
    assert C.misplaced_names("we visited Port Odette and the Paloma campus")
    assert C.misplaced_names("The deal is in the West desk.") == []
    assert C.addressed_elsewhere("Elena don't think so", "Owen")
    assert C.addressed_elsewhere("Great job Lorcan. Thanks", "Owen")
    assert not C.addressed_elsewhere("Juno has asked me", "Helena")
    assert C.is_greeting_line("Hi Helena,") and not C.is_greeting_line("Please call me.")
    cases = [
        (("board", "I will give the form to the chairman on Monday."),
         {"sender_id": "board_chair"}, "own-role-in-third-person"),
        (("board", "Please print in color for me before the board meeting."),
         {"to_ids": ("cbo",)}, "clerical-request-to-executive"),
        (("director", "The letter of credit for the counterparty is attached."),
         {"to_ids": ("director_hr",)}, "finance-for-hr"),
        (("director", "These deals were input as not to be confirmed."),
         {"to_groups": ("external",)}, "back-office-to-outsider"),
        (("exec", "It was an honor to represent the team; thank you for having me."),
         {"sender_id": "ceo"}, "deference-from-ceo"),
        (("assistant", "The storage proxy schedules for the pipeline are attached."), {},
         "operations-from-assistant-or-board"),
    ]  # fmt: skip
    for (group, text), kw, reason in cases:
        assert C.role_reason(group, text, **kw) == reason, reason
