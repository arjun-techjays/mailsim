"""Realism and residual-PII fixes: cleaning of charset damage, scrubber and scanner forms,
common words never pseudonymized, organisation pseudonyms, date normalisation, the message
frame (greeting, sign-off, signature block), role coherence, generated attachments and the
attachment-aware scanner. All names, firms and numbers here are made up."""

from __future__ import annotations

import email
import io
import re
import zipfile
from datetime import UTC, date, datetime
from email import policy

import pytest

from mailsim.domain.models import Address
from mailsim.importers import curation as C
from mailsim.importers import dates as D
from mailsim.importers import documents as DOC
from mailsim.importers import names as N
from mailsim.importers.attachments import (
    Line,
    extract_text,
    pdf_bytes,
    pdf_text,
    xlsx_bytes,
    xlsx_text,
)
from mailsim.importers.clean import clean_body
from mailsim.importers.pii import Scanner
from mailsim.importers.pseudonymize import Harvester, Pseudonymizer, Renderer
from mailsim.mime.build import build_mime
from mailsim.mime.parse import ParsedAttachment
from tests.importers.conftest import TEST_KEY


def _pseudo(text: str = "") -> Pseudonymizer:
    h = Harvester()
    h.feed("From: Wendell Quarrick <wendell.quarrick@enron.com>\n" + text)
    return Pseudonymizer(h.finish(), 5, TEST_KEY)


# ----------------------------------------------------------------------- cleaning (D-9)


def test_charset_damage_is_repaired():
    raw = (
        "Enron,s plan. I,m in. everyone,s view and HR,s value. a,b list, s.\n"
        "this morning.? He is thrilled.? I have it\nNew York, NY? 10055\n?\n"
        "?https://x.example.com/a\n? first item\n(gas)\\\\\nWhat? 5 dollars"
    )
    out = clean_body(raw)
    assert "Enron's plan. I'm in. everyone's view and HR's value. a,b list, s." in out
    assert "this morning. He is thrilled. I have it" in out
    assert "NY 10055" in out and "\n?\n" not in out
    assert "\nhttps://x.example.com/a" in out and "- first item" in out
    assert "(gas)\n" in out and "What? 5 dollars" in out


# -------------------------------------------------------------- scrubber + scanner (D-3)


def test_streets_glued_phones_alnum_ids_and_acronyms_are_scrubbed():
    p = _pseudo()
    text = p.apply(
        "Call Tel.713-555-8812 or Fax:713-555-8813. Deal QX4417 and QX4417.2, Z90210.A, "
        "FY2026, Q3.\nAcme Barrow, LLC\n55 East 52nd Street\nNew York, NY 10055\n"
        "1221 N. Lamar Blvd. and 7 Elm Avenue\nunpaid bills on TW. TW Commercial. TWh TW/JS\n"
        "Sign up for enerfaxdaily today. EB3801a"
    ).text
    assert "713" not in text and "(555) 555-01" in text
    assert "QX4417" not in text and re.search(r"CX\d{4}\.2", text)
    assert "FY2026" in text and "Z90210" not in text
    assert "52nd" not in text and "Lamar" not in text and "Elm Avenue" not in text
    assert text.count("100 Commerce Street") == 3 and "79999" in text
    assert "WL Commercial" in text and "on WL." in text and "TWh TW/JS" in text
    assert "gridwiredaily" in text and "CT 3801a" in text
    assert Scanner().scan_text(text) == []


def test_scanner_flags_the_missed_forms():
    text = (
        "Tel.713-555-8812\nDeal QX4417\n55 East 52nd Street\n100 Commerce Street\n"
        "bills on TW.\nenerfaxdaily, Prebon and the Astros\nCoral transacted via APB, not coral reefs\nafter 15 years as CEO\n"
        "P.O. Box 1234 or P.O. Box 100\nNew York, NY? 10055\nlunch at the Galleria"
    )
    kinds = sorted({(f.kind, f.match) for f in Scanner().scan_text(text)})
    assert ("phone", "713-555-8812") in kinds and ("deal-id", "QX4417") in kinds
    assert ("street", "55 East 52nd Street") in kinds and ("street", "P.O. Box 1234") in kinds
    assert ("enron-acronym", "TW") in kinds and ("third-party", "Astros") in kinds
    assert ("third-party", "enerfaxdaily") in kinds and ("third-party", "Prebon") in kinds
    assert ("third-party", "Coral") in kinds and ("third-party", "APB") in kinds
    assert ("third-party", "coral") not in kinds
    assert ("famous-record", "years as CEO") in kinds and ("landmark", "Galleria") in kinds
    assert any(k == "zip" for k, _ in kinds)
    assert not any(m in ("100 Commerce Street", "P.O. Box 100") for _, m in kinds)


def test_third_parties_map_to_fictional_names():
    text = (
        _pseudo()
        .apply(
            "Thanks for the Eyes of Texas note; A&M lost. Rite Aid and the WEF. "
            "Prebon Yardley's party and Napster. Booked in Sitara and TAGG."
        )
        .text
    )
    assert Scanner().scan_text(text) == []
    for real in ("Texas A", "Rite Aid", "WEF", "Prebon", "Napster", "Sitara", "TAGG"):
        assert real not in text
    assert "Brannock Tull" in text and "Gulf State" in text


# --------------------------------------------------------- common words, orgs (D-8)


def test_common_words_and_places_are_never_pseudonymized():
    h = Harvester()
    h.feed(
        '"Union, Federal" <fu@x.org>\nFrom: Council, Global\nTo: Christi, Corpus\n'
        "From: Wendell Quarrick <wendell.quarrick@enron.com>\n"
    )
    reg = h.finish()
    assert reg.lookup("Federal", "Union") is None and reg.lookup("Global", "Council") is None
    assert reg.lookup("Corpus", "Christi") is None
    p = Pseudonymizer(reg, 5, TEST_KEY)
    text = p.apply(
        "The Corvane Federal Credit Union board meets. The Global Business Council and the "
        "Rate and Currency Risk Desk. The meeting held in Kansas City and Corpus Christi.\n"
        "Pacific Gas & Electric Company and Wendell Quarrick agree."
    ).text
    assert "Federal Credit Union" in text and "Global Business Council" in text
    assert "Currency Risk Desk" in text and "Kansas City" in text and "Corpus Christi" in text
    assert "Pacific Gas & Electric Company" in text and "{{p:" in text


def test_firm_names_become_organisation_pseudonyms():
    p = _pseudo()
    text = p.apply(
        "Blue Flame Propane Inc. sent the Master Agreement. Coast Energy Group, a division of "
        "Bramble Propane, L.P., and The Ostrander Holdings. Corvane North America Corp. and "
        "Stellaris Inc. and Bank of America Corp. stay."
    ).text
    assert "Blue Flame" not in text and "Coast" not in text and "Ostrander" not in text
    assert "{{o:0:n}} Inc." in text and "{{o:1:n}} Energy Group" in text
    assert "Corvane North America Corp." in text and "Stellaris Inc." in text
    assert "Bank of America Corp." in text
    r = Renderer(5)
    r.new_scope(["Helena Marsh"])
    out = r.render(text)
    orgs = [r.identity(f"o:{i}").first for i in range(3)]
    assert len(set(orgs)) == 3 and all(o in N.FAKE_ORG_NAMES for o in orgs)
    assert r.identity("o:1").email.endswith(".example.test")
    assert all(o in out for o in orgs)


def test_pools_are_disjoint_and_free_of_known_real_names():
    people = set(N.FAKE_FIRST) | set(N.FAKE_LAST)
    assert not people & set(N.FAKE_ORG_NAMES)
    assert not {"Yardley", "Quayle", "Faraday"} & people
    from mailsim.importers.leadership import ROSTER

    persona_words = {w for p in ROSTER for w in p.name.split()}
    assert not persona_words & set(N.FAKE_ORG_NAMES)


def test_possessive_ambiguous_first_name_is_a_person():
    text = _pseudo().apply("I was surprised at Bill's reaction. Mark the date.").text
    assert "Bill's" not in text and "{{q:" in text and "Mark the date" in text


# ------------------------------------------------------------------------ dates (D-4)


def test_dates_move_by_whole_weeks_and_stay_consistent():
    text = (
        "The meeting set for Wednesday, July 5, is cancelled; it resumes Monday, July 10, 2000. "
        "Credit Watch as of 6/23/00 and 07/01/2000 (2000-06-30). Budget for Q3 2000, a great "
        "2000. Summer 01 strip. 2000 MW at deal 2000. Due on 6/30 and on 07/03.\n6/28 review"
    )
    shift = D.plan([text], date(2026, 10, 12), date(2026, 10, 15))
    assert shift.days % 7 == 0 and shift.anchor == date(2000, 7, 10)
    out = D.apply(text, shift)
    moved = date(2000, 7, 10) + (shift.anchor and (date(2000, 7, 10) - shift.anchor))
    assert moved
    m = re.search(r"Monday, (\w+) (\d+), (\d{4})", out)
    assert m is not None and m.group(3) == "2026"
    new = datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%B %d %Y").date()
    assert new.weekday() == 0 and new <= date(2026, 10, 15)
    assert re.search(r"Wednesday, \w+ \d+,", out)
    assert "Q3 2026" in out and "great 2026" in out and "Summer 27" in out
    assert "2000 MW" in out and "deal 2000" in out
    assert not re.search(r"\b(?:19|20)0\d\b(?! MW)(?<!deal 2000)", out.replace("deal 2000", ""))
    assert re.search(r"as of \d{1,2}/\d{1,2}/26\b", out) and re.search(r"\d{2}/\d{2}/2026", out)
    assert re.search(r"\(2026-\d\d-\d\d\)", out) and re.search(r"(?m)^\d{1,2}/\d{1,2} review", out)


def test_dates_without_anchor_and_textual_forms():
    assert D.plan(["nothing dated here"], date(2026, 10, 1), date(2026, 10, 15)) == D.Shift()
    assert D.apply("no change", D.Shift()) == "no change"
    years_only = D.plan(
        ["the 2001 budget and the 2002 plan"], date(2026, 10, 1), date(2026, 10, 15)
    )
    assert years_only.years == 24 and years_only.days == 0
    assert D.apply("the 2001 budget and the 2002 plan, March of 2001", years_only) == (
        "the 2025 budget and the 2026 plan, March of 2025"
    )
    text = ("Held November 12 & 13, 2001 and moved to Jan. 17th - 18th, 2002; report for "
            "08 December 2001, June 2001 results; Oct. 16th call; Sept. 30, 2001 close")  # fmt: skip
    shift = D.plan([text], date(2026, 10, 9), date(2026, 10, 15))
    out = D.apply(text, shift)
    assert "2001" not in out and "2002" not in out
    assert re.search(r"\w{3}\. \d+(?:st|nd|rd|th) - \d+(?:st|nd|rd|th), 2026", out)
    assert re.search(r"\d\d \w+ 202\d", out) and re.search(r"\w{3}\. \d+\w\w call", out)
    # ranges crossing a month and impossible days
    shift2 = D.Shift(days=7, years=0, reference=2001, anchor=date(2001, 1, 30))
    assert D.apply("January 30 - 31, 2001", shift2) == "February 6 - 7, 2001"
    assert D.apply("January 28 - 31, 2001", D.Shift(days=3, years=0, reference=2001,
                                                    anchor=date(2001, 1, 28))) == (
        "January 31 - February 3, 2001"
    )  # fmt: skip
    assert D.apply("February 30, 2001 and 13/45/2001", shift2) in (
        "March 7, 2001 and 13/45/2001",
        "March 8, 2001 and 13/45/2001",
    )


# --------------------------------------------------------------- frame + role (D-7)


def test_greeting_and_signature_are_rewritten_for_the_personas():
    template = (
        "Dear Mr. {{s:4:l}},\n\nPlease review the draft letter of credit before Friday.\n\n"
        "Best regards,\n{{p:7:n}}\nVice President, Structured Finance\n{{o:2:n}} Capital LLC\n"
        "100 Commerce Street\n(555) 555-0101 (Phone)\nMake every day count."
    )
    out = C.reframe(template, to_first="Helena", sender_first="Rohan")
    assert out == (
        "Dear Helena,\n\nPlease review the draft letter of credit before Friday.\n\n"
        "Best regards,\nRohan"
    )
    lotus = (
        "{{q:1:f}} -\n\nThe report is late again.\n\n{{p:2:n}}. {{q:3:f}}\nCT 3892\n(555) 555-0188"
    )
    assert C.reframe(lotus, to_first="Owen", sender_first="Natalie") == (
        "Owen,\n\nThe report is late again.\n\nNatalie"
    )
    group = "{{q:1:f}}, {{q:2:f}} and {{q:3:f}}:\n\nPlease see the schedule.\n\nthanks\nbrant"
    assert C.reframe(group, to_first="Owen", sender_first="Priya", single=False) == (
        "Hi all:\n\nPlease see the schedule.\n\nThanks,\nPriya"
    )
    # one recipient: a greeting to several people or to a group names that recipient
    assert C.reframe(group, to_first="Owen", sender_first="Priya") == (
        "Owen:\n\nPlease see the schedule.\n\nThanks,\nPriya"
    )
    assert C.rewrite_greeting("Hi all,\n\nThe numbers are in.", "Owen") == (
        "Hi Owen,\n\nThe numbers are in."
    )
    assert C.rewrite_greeting("Guys, have we started the work?", "Julia") == (
        "Julia, have we started the work?"
    )
    assert C.rewrite_greeting("Hi all,\n\nThe numbers are in.", "Owen", single=False) == (
        "Hi all,\n\nThe numbers are in."
    )
    inline = "Hi {{q:1:f}}. Just checking in on the numbers for today.\nGood end to the week. DF"
    assert C.reframe(inline, to_first="Rohan", sender_first=None) == (
        "Hi Rohan. Just checking in on the numbers for today.\nGood end to the week."
    )
    keep = "Ladies and Gentlemen:\n\nThe forum starts at nine.\n\nEdward"
    assert C.reframe(keep, to_first="Helena", sender_first="Edward").startswith("Ladies and")
    assert C.rewrite_greeting("", "Helena") == ""


def test_role_coherence_rules():
    assert C.role_reason("exec", "I am an MBA candidate, class of 2026.") == "applicant"
    assert C.role_reason("exec", "Click here to unsubscribe from this list.") == "mass-mail"
    issue = "This week's edition: gas storage report. " + "Prices rose on the hub today. " * 10
    assert C.role_reason("newsletter", issue) is None
    assert C.role_reason("newsletter", "This week's edition: short.") == "not-a-newsletter"
    assert C.role_reason("newsletter", "I have a very important video announcement.") == (
        "not-a-newsletter"
    )
    assert C.role_reason("board", "You can sort the files by format.") == "off-topic-for-board"
    assert C.role_reason("board", "You can sort the music files by format.") == "off-role"
    assert C.role_reason("board", "The committee meets on the budget.") is None
    assert C.role_reason("director", "My name is {{p:1:n}} with the call center.") == (
        "introduction"
    )
    assert C.role_reason("assistant", "Love, Dad") == "off-role"
    assert C.role_reason("external", "Happy birthday to the kids!") is None
    assert C.self_reference("Please RSVP to {{p:3:n}} by Friday.", ["p:3"])
    assert not C.self_reference("Please RSVP to {{p:4:n}}.", ["p:3"])


def test_junk_and_truncated_records_are_rejected():
    def seg(body, subject=""):
        return {"body": body, "subject": subject}

    ok = "Can you send me the revised forecast for the plant by Friday?"
    assert C.junk_reason([seg(ok)]) is None
    assert C.junk_reason([seg(ok, "Tax on energy profits.url")]) == "url-subject"
    assert C.junk_reason([seg("https://www.x.example.test/")]) == "near-empty"
    assert C.junk_reason(
        [
            seg(
                "Please review the numbers today.\n?https://www.x.example.test/ holds the latest copy of the plan we discussed"
            )
        ]
    ) == ("damaged")
    cut = "The basis for requesting expedited status is that the agreement includes a provision"
    assert C.junk_reason([seg(cut)]) == "truncated"
    assert C.junk_reason([seg("We will look at it and get back to the")]) == "truncated"
    assert C.junk_reason([seg(ok + " Save 70% today!!!")]) == "spam"
    assert C.junk_reason([seg(ok + "\nIf you are not the intended recipient")]) == "disclaimer"
    assert C.junk_reason([seg(ok + "\n<HTML><BODY>")]) == "html"
    assert C.junk_reason([seg(ok + " Happy Thanksgiving!")]) == "holiday"
    assert C.junk_reason([seg(ok + "\n_______________\nquoted text here")]) == "quoted-block"
    assert C.junk_reason([seg("News Brief: the firm buys a storage hub today.")]) == "newswire"
    assert not C.truncated("{{p:1:f}}")


def test_subjects_are_derived_from_content():
    assert C.derive_subject("Hi {{q:1:f}},\n\nAttached is the revised credit watch listing as "
                            "of today. Please review.") == "The revised credit watch listing"  # fmt: skip
    assert C.derive_subject("Executive Committee Weekly Meeting\n\nDate: Tuesday") == (
        "Executive Committee Weekly Meeting"
    )
    assert C.derive_subject("Imagine, if you can, a world in which information\nis "
                            "disseminated as rapidly as it is received.") == (
        "A world in which information is disseminated"
    )  # fmt: skip
    assert C.derive_subject("https://www.x.example.test/", "personal") == "Catching up"


# --------------------------------------------------------------- attachments (D-2)


def _check_pdf_structure(data: bytes) -> int:
    """Header, xref offsets pointing at their objects, trailer size, startxref, %%EOF."""
    assert data.startswith(b"%PDF-1.4\n") and data.rstrip().endswith(b"%%EOF")
    startxref = int(re.search(rb"startxref\n(\d+)\n%%EOF", data).group(1))
    assert data[startxref:].startswith(b"xref\n")
    count = int(re.match(rb"xref\n0 (\d+)\n", data[startxref:]).group(1))
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf]) \n", data[startxref:])
    assert len(entries) == count
    for no, (off, _gen, kind) in enumerate(entries):
        if kind == b"n":
            assert data[int(off) :].startswith(f"{no} 0 obj".encode())
    assert re.search(rf"/Size {count}\b".encode(), data)
    for m in re.finditer(rb"<< /Length (\d+) >>\nstream\n", data):
        end = m.end() + int(m.group(1))
        assert data[end:].startswith(b"\nendstream")
    return int(re.search(rb"/Count (\d+)", data).group(1))


def test_pdf_writer_is_valid_multipage_and_extractable():
    pages = [[Line("title", "Plan (draft) \\ v2"), Line("p", "word " * 900)],
             [Line("h1", "Table"), Line("mono", "a" * 200), Line("x", "unknown style")]]  # fmt: skip
    data = pdf_bytes(pages, title="Plan", author="Rohan Desai", created="20261009120000")
    assert _check_pdf_structure(data) >= 3  # the long paragraph flowed onto a second page
    text = pdf_text(data)
    assert "Plan (draft) \\ v2" in text and "Page 1 of" in text and "Rohan" not in text.split()[0]
    assert pdf_bytes(pages, title="Plan", author="A", created="1") == pdf_bytes(
        pages, title="Plan", author="A", created="1"
    )
    import zlib

    flate = (b"%PDF-1.4\n1 0 obj\n<< /Length 9 /Filter /FlateDecode >>\nstream\n"
             + zlib.compress(b"(Hidden \\101) Tj") + b"\nendstream\nendobj\n"
             )  # fmt: skip
    assert "Hidden A" in pdf_text(flate)
    from mailsim.importers.attachments import UnreadableAttachment

    with pytest.raises(UnreadableAttachment):
        pdf_text(flate + b"2 0 obj\n<< /Filter /FlateDecode >>\nstream\nnot-zlib\nendstream\n")


def test_xlsx_writer_and_text_extraction():
    data = xlsx_bytes([["Item", "Amount"], ["Gas & power", 12.5], [3, "x<y"]], sheet="Deals")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert "xl/worksheets/sheet1.xml" in zf.namelist()
        assert all(i.date_time == (2026, 1, 1, 0, 0, 0) for i in zf.infolist())
    text = xlsx_text(data)
    assert "Gas & power" in text and "12.5" in text
    assert extract_text(data, "a.xlsx") == text
    from mailsim.importers.attachments import UnreadableAttachment

    with pytest.raises(UnreadableAttachment):
        extract_text(b"PK broken", "a.xlsx")
    assert extract_text(b"plain words", "a.txt") == "plain words"


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Attached is a revised Credit Watch listing", "credit_watch"),
        ("Please pay the attached invoice", "invoice"),
        ("See the attached spreadsheet", "spreadsheet"),
        ("Attached is the draft master agreement", "contract"),
        ("Attached is the updated Executive Committee List", "roster"),
        ("Attached is the agenda for Thursday", "agenda"),
        ("Attached is the weekly P&L report", "report"),
        ("Attached are my notes", "memo"),
    ],
)
def test_documents_for_every_kind(text, kind):
    assert DOC.kind_of(text) == kind
    ctx = DOC.Context(7, "k1", f"RE: {text}", text + ". Thanks.", "Rohan Desai",
                      "Chief Financial Officer", "Helena Marsh",
                      datetime(2026, 10, 9, 10, tzinfo=UTC))  # fmt: skip
    doc = DOC.build(kind, ctx)
    assert len(doc.data) < 100_000 and doc.filename.startswith(DOC._slug(DOC._title(ctx.subject)))
    if kind == "spreadsheet":
        assert doc.filename.endswith(".xlsx") and "Rohan Desai" in xlsx_text(doc.data)
    else:
        assert doc.content_type == "application/pdf" and _check_pdf_structure(doc.data) >= 2
        assert "Rohan Desai" in pdf_text(doc.data) or kind == "invoice"
    assert DOC.build(kind, ctx).data == doc.data  # deterministic
    assert Scanner().scan_text(extract_text(doc.data, doc.filename)) == []


def test_board_deck_is_a_real_deck():
    ctx = DOC.Context(7, "ceo:risk:board", "CONFIDENTIAL: Board pre-read", "Attached is the "
                      "pre-read.", "Edward Kincaid", "Board Chair", "Helena Marsh",
                      datetime(2026, 10, 16, 7, tzinfo=UTC))  # fmt: skip
    doc = DOC.build("board_deck", ctx, filename_title="Larkspur-board-pre-read")
    assert doc.filename == "Larkspur-board-pre-read.pdf"
    assert _check_pdf_structure(doc.data) == 8
    text = pdf_text(doc.data)
    assert "Project Larkspur" in text and "Valuation" in text and "BOARD CONFIDENTIAL" in text


def test_stub_lines_and_claims():
    body = "Attached is the brief.\n\n <<x5503.DOC>>\n\n - x5503.DOC\nThanks"
    assert DOC.STUB_LINE.sub("", body).count("x5503") == 0
    assert DOC.ATTACH_CLAIM.search("Here is the latest version of the letter.")
    assert not DOC.ATTACH_CLAIM.search("Here is what I think.")


def test_scanner_reads_decoded_bodies_and_attachment_text(tmp_path):
    pdf = pdf_bytes([[Line("p", "Call Tel.713-555-8812 at 55 East 52nd Street")]], title="t",
                    author="a", created="1")  # fmt: skip
    raw = build_mime(
        message_id="<a@b.example.test>",
        from_=Address(email="a@b.example.test", name="A"),
        to=[Address(email="c@d.example.test", name="C")],
        subject="Plan",
        date=datetime(2026, 10, 9, tzinfo=UTC),
        text=("long line " * 20) + "deal QX4417 " + ("tail " * 20) + "\n",
        attachments=[ParsedAttachment("plan.pdf", "application/pdf", pdf, False, None)],
    )
    msg = email.message_from_bytes(raw, policy=policy.default)
    assert msg.get_body(("plain",)) is not None
    (tmp_path / "m.eml").write_bytes(raw)
    (tmp_path / "notes.txt").write_text("Prebon")
    found = {(f.kind, f.match) for f in Scanner().scan_paths([tmp_path])}
    assert ("deal-id", "QX4417") in found and ("phone", "713-555-8812") in found
    assert ("street", "55 East 52nd Street") in found and ("third-party", "Prebon") in found


def test_more_frame_and_curation_rules():
    assert C.reframe("JW,\n\nShould I roll the flow?\n\nVery truly yours;\n{{p:1:n}}",
                     to_first="Sam", sender_first="Priya") == (
        "Sam,\n\nShould I roll the flow?\n\nVery truly yours,\nPriya"
    )  # fmt: skip
    one_line = "See https://intranet.example.test/ for the merger update and questions."
    assert C.strip_signature(one_line)[0] == one_line
    listing = "Markets:\n{{q:1:f}}\n{{q:2:f}}\n{{p:3:n}}\nPJM\nThanks for the data."
    assert C.junk_reason([{"body": listing, "subject": ""}]) == "name-list"
    assert C.role_reason("newsletter", "This week's edition of Corvane Digital News.") == (
        "not-a-newsletter"
    )
    assert C.role_reason("external", "Please send the meter forecast.", "personal") == (
        "business-from-a-friend"
    )
    assert (
        C.derive_subject("FYI - {{q:1:f}} will not be in today.")
        == "{{q:1:f}} will not be in today"
    )
    assert C.derive_subject("Dear Mr. {{s:1:l}}:\n\nPlease review the draft master agreement.") == (
        "Please review the draft master agreement"
    )


def test_attachment_claims_are_precise_and_stub_titles_readable():
    assert not DOC.ATTACH_CLAIM.search("I was so attached and impressed with your work.")
    for claim in ("Attached is the plan.", "Please see the attached.", "I have attached it.",
                  "Enclosed is a draft.", "<<plan.doc>>", "Here is the latest version."):  # fmt: skip
        assert DOC.ATTACH_CLAIM.search(claim), claim
    assert DOC.stub_title("<<Comparison of Turbine Agreement.DOC>>") == (
        "Comparison of Turbine Agreement"
    )
    assert DOC.stub_title(" - wlu dec th.doc") is None and DOC.stub_title("<<x5503.DOC>>") is None


def test_scrubber_phone_and_address_edge_cases():
    text = (
        _pseudo()
        .apply(
            "Mobile +44 1105695188. Quote sheet for 10/1/01-9/30/11 volumes. "
            "[Bill, can you comment?] Ashgrove Gas Corp. (it is a Delaware Corp.)"
        )
        .text
    )
    assert "1105695188" not in text and "(555) 555-01" in text
    assert "10/1/01-9/30/11" in text
    assert "[Bill," not in text and "Delaware Corp." in text


def test_scanner_flags_lowercase_signature_names_and_real_surname_pairs():
    s = Scanner(["Brantley Okonkwo", "Rick Tamsworth"])
    found = {
        (f.kind, f.match) for f in s.scan_text("thanks\nbrantley\nok\n-brantley\nSilas Tamsworth")
    }
    assert ("real-name", "brantley") in found
    assert ("real-name", "brantley") in {
        (f.kind, f.match) for f in s.scan_text("hey brantley -- ok")
    }
    assert not s.scan_text("thanks\nregards")
    assert ("real-surname", "Silas Tamsworth") not in found  # not a KNOWN_PEOPLE surname
    assert any(f.kind == "real-surname" for f in Scanner().scan_text("Ask Tobias Kitchen today"))


def test_month_year_mentions_anchor_the_shift():
    text = "Use FIX in parallel for December 2000 and under fire for January 2001 invoices."
    shift = D.plan([text], date(2026, 10, 9), date(2026, 10, 15))
    out = D.apply(text, shift)
    assert shift.anchor == date(2001, 1, 15)
    assert out == "Use FIX in parallel for September 2026 and under fire for October 2026 invoices."


def test_date_edge_cases():
    shift = D.Shift(days=7, years=1, reference=2001, anchor=date(2001, 3, 1))
    assert D.apply("2001-13-45 and 2001-03-02", shift) == "2001-13-45 and 2001-03-09"
    assert D.apply("on 3/2 and on 13/40", shift) == "on 3/9 and on 13/40"
    assert (
        D.apply("Mon., Mar. 5th and Sept. 3, 2001", shift) == "Mon., Mar. 12th and Sept. 10, 2001"
    )
    assert D.apply("Monday, March 5 to 3", shift) == "Monday, March 12 to 3"
    assert D.apply("05/06/01 and 5/6/2001", shift) == "05/13/01 and 5/13/2001"
    no_anchor = D.Shift(days=0, years=25)
    assert D.apply("on 3/2, March 5 and Summer 01, FY01", no_anchor) == (
        "on 3/2, March 5 and Summer 26, FY26"
    )
    assert D._infer_year(2, 30, 0) is None and D._infer_year(3, 5, None) is None
    assert D._place(3, 5, D.Shift(days=7), None) is None


def test_late_curation_rules():
    log = "11:15:39 Synchronizing Mailbox\n11:15:40 Synchronizing Folder 'Inbox'\nCanceled"
    assert C.junk_reason([{"body": log, "subject": ""}]) == "damaged"
    semis = "The phones will be down tonight for an hour.\n;\n;\n"
    assert C.junk_reason([{"body": semis, "subject": ""}]) == "damaged"
    assert C.role_reason("external", "Hello! x has just sent you a greeting card!") == "mass-mail"
    assert DOC.stub_title("(See attached file: Radio Tower Lease Agreement.doc)") == (
        "Radio Tower Lease Agreement"
    )
    assert C.derive_subject("Thanks, {{q:1:f}}\n\nThe call is at two tomorrow afternoon.") == (
        "The call is at two tomorrow afternoon"
    )
    assert C.derive_subject("We parked 25,000 dt/day on the 25th-27th, as agreed.") == (
        "We parked 25,000 dt/day on the 25th-27th"
    )
    shift = D.Shift(days=0, years=25)
    assert D.apply("permits by Sep '02 if we start in July '01", shift) == (
        "permits by Sep '27 if we start in July '26"
    )


def test_late_scrubber_and_scanner_rules():
    text = (
        _pseudo()
        .apply(
            "Call X-31450. MARK YOUR LUNCH CALENDARS NOW. Ship to 750 B Street, San Diego.\n"
            "Author (1836-1902) wrote it."
        )
        .text
    )
    assert "31450" not in text and "MARK YOUR LUNCH" in text and "750 B Street" not in text
    assert "(1836-1902)" in text
    found = Scanner().scan_text("we will review it with Tobias\nKitchen and the team")
    assert any(f.kind == "real-surname" for f in found)


def test_persona_first_names_are_in_sync_and_known_first_names_flagged():
    from mailsim.importers.leadership import ROSTER

    firsts = {p.first for p in ROSTER if p.group not in ("newsletter", "unknown")}
    assert firsts - {"Nimbus"} <= N.PERSONA_FIRST | {"Energy"}
    found = Scanner().scan_text("Thanks\n\nKenneth\n")
    assert any(f.kind == "real-name" and f.match == "Kenneth" for f in found)
    assert not Scanner().scan_text("Thanks\n\nHelena\n")


def test_curation_edge_branches():
    assert C.truncated("We discussed the numbers today.\nHome phone numbers:")
    assert C.truncated("{{p:1:n}}:\nDetails follow below today.\nNotes:")
    body = "Agenda for the call:\n15 Minutes - Grant Plover\n30 Minutes - {{q:1:f}}"
    assert C.junk_reason([{"body": body, "subject": ""}]) == "residual-name"
    assert C.role_reason("director", "I will depose the plaintiff for my client.") == "lawyer-voice"
    assert C.role_reason("exec", "my client", "lawyer") is None
    sig = "The plan is fine.\n\nThanks,\n{{p:1:n}}\nTel: (555) 555-0101\n<Microsoft Clip Gallery>"
    assert C.strip_signature(sig) == ("The plan is fine.", "Thanks")
    long_after = "The plan is fine.\n\nThanks,\n" + "\n".join(
        f"line {i} of notes." for i in range(14)
    )
    assert C.strip_signature(long_after)[1] is None
    assert C.derive_subject("Write to {{x:1:e}} today\n\nGood morning,\n\nThe budget review is "
                            "(mostly) done for the plant now.") == (
        "The budget review is (mostly) done"
    )  # fmt: skip
    assert C.derive_subject("Hi,\n\nOK.\n\nThe plan (draft is ready for the board") == (
        "The plan draft is ready"
    )
