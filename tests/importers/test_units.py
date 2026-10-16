"""Unit tests: chain splitting, cleaning, classification, pseudonymization and the scanner."""

from __future__ import annotations

from mailsim.importers import names as N
from mailsim.importers.classify import GENERAL, classify
from mailsim.importers.clean import clean_body, is_garbled, split_chain
from mailsim.importers.pii import Scanner
from mailsim.importers.pseudonymize import (
    PLACEHOLDER,
    Harvester,
    Pseudonymizer,
    Renderer,
    fake_identity,
    refs_in,
    unknown_proper_nouns,
)

LOTUS_CHAIN = (
    "Grace,\n\nPlease, put it on my schedule.\n\nWendell\n\n"
    "---------------------- Forwarded by Wendell Quarrick/HOU/ECT on 02/12/2001 \n"
    "03:12 PM ---------------------------\n   \n\n\n"
    "From:  Marta Pellowe @ ENRON                                    \n"
    "               02/06/2001 01:14 PM\t\n\t\n\t\n\t      \n\t\n\n"
    "To: Wendell Quarrick/HOU/ECT@ECT\ncc: Osgood Brannick/HOU/ECT@ECT, \n"
    "  Livia Tazewell/HOU/ECT@ECT\nSubject: Trading limits\n\nWendell -\n\nHere is the summary."
    "\n\nRegards,\nMarta\n"
)
OUTLOOK_CHAIN = (
    "Marta -\n\nAgreed.\n\nWendell\n\n -----Original Message-----\nFrom: \tPellowe, Marta  \n"
    "Sent:\tMonday, October 02, 2000 9:15 AM\nTo:\tQuarrick, Wendell; Brannick, Osgood\n"
    "Subject:\tRE: budget\n\nWendell,\n\nCan you check the budget?\n\nMarta\n"
)
LOTUS_REPLY = (
    "Ok.\n\n\n\n\nMarta Pellowe\n10/02/2000 09:15 AM\nTo: Wendell Quarrick/HOU/ECT@ECT\ncc: \n"
    "Subject: numbers\n\nWendell,\n\nnumbers attached.\n\nMarta\n\n\n"
    '"Ferris Ockenden" <ferris@partner.com> on 10/01/2000 08:00:00 AM\n'
    "To: marta.pellowe@enron.com\ncc: \nSubject: request\n\nplease send the numbers.\n"
)


def test_split_lotus_forward_chain():
    top, quoted = split_chain(LOTUS_CHAIN)
    assert "put it on my schedule" in top.body and quoted.headers is not None
    h = quoted.headers
    assert (h.kind, h.subject, h.forwarded_by) == ("forward", "Trading limits",
                                                    "Wendell Quarrick/HOU/ECT")  # fmt: skip
    assert h.from_ == "Marta Pellowe @ ENRON" and h.date == "02/06/2001 01:14 PM"
    assert "Livia Tazewell" in h.cc and "Here is the summary" in quoted.body


def test_split_outlook_and_lotus_reply_chains():
    top, quoted = split_chain(OUTLOOK_CHAIN)
    h = quoted.headers
    assert (h.kind, h.from_, h.subject) == ("reply", "Pellowe, Marta", "RE: budget")
    assert h.to.startswith("Quarrick, Wendell;") and h.date.startswith("Monday")
    segs = split_chain(LOTUS_REPLY)
    assert [s.headers.subject if s.headers else None for s in segs] == [None, "numbers", "request"]
    assert segs[1].headers.from_ == "Marta Pellowe"
    assert segs[2].headers.from_.startswith('"Ferris Ockenden"')
    assert split_chain("no chain here\nSubject: just text")[0].headers is None


def test_clean_body_and_garbled():
    raw = (
        "Hi,\r\nwe don?t know = \r\nthe=20answer &amp; more ? really\x07\n> quoted\n\n\n\nok\n"
        + "\n" * 3
        + "body line\n" * 4
        + "-" * 30
        + "\nThis e-mail is confidential and intended "
        "for the intended recipient only.\n"
    )
    out = clean_body(raw)
    assert "don't" in out and "&" in out and "- really" in out and "quoted" not in out
    assert "intended recipient" not in out and "\n\n\n" not in out
    kept = clean_body(raw, strip_quotes=False, strip_disclaimers=False)
    assert "> quoted" in kept and "intended recipient" in kept
    assert is_garbled("x") and is_garbled("1234 5678 9012 " * 5) and is_garbled("é" * 40)
    assert is_garbled("A" * 70 + " " + "B" * 70 + " " + "C" * 70)
    assert not is_garbled("A perfectly ordinary sentence about the budget review.")


def test_classifier_is_transparent():
    c = classify("Please review the budget and the invoice payment before the audit.")
    assert c.topic == "finance" and "budget" in c.keywords["finance"]
    assert c.scores["finance"] >= 7
    assert classify("Board meeting agenda for the board of directors").topic == "board"
    assert classify("nothing relevant at all").topic == GENERAL
    tie = classify("lunch")  # scheduling only
    assert tie.topic == "scheduling" and tie.scores == {"scheduling": 2}


def _pseudo(text: str, seed: int = 5) -> Pseudonymizer:
    h = Harvester()
    h.feed(text)
    h.feed("the mike is the word. " * 3)
    return Pseudonymizer(h.finish(), seed)


def test_harvest_and_template():
    corpus = (
        LOTUS_CHAIN + OUTLOOK_CHAIN + LOTUS_REPLY + "Paula Stonebridge/HOU/ECT@ECT\n"
        "Outlook Migration Team/Corp/Enron@ENRON\n"
    )
    p = _pseudo(corpus)
    reg = p.reg
    assert (
        reg.lookup("Marta", "Pellowe") is not None and reg.lookup("Ferris", "Ockenden") is not None
    )
    assert reg.lookup("Paula", "Stonebridge") is not None  # common first name, ordinary surname
    assert reg.lookup("Outlook", "Team") is None
    assert reg.lookup("Vince", "Kaminski") is not None  # seed list
    marta = reg.lookup("Marta", "Pellowe")
    t = p.apply(
        "Hi Marta,\n\nMarta Pellowe and Pellowe, Marta and Mr. Pellowe; mail "
        "marta.pellowe@enron.com or Marta Pellowe/HOU/ECT@ECT. Mark the date; Marta's file.\n"
        "Enron North America, EnronOnline, http://www.enron.com/x and partner.com.\n"
        "Call (713) 853-1234, 853-1234, x34567, +44 20 7783 6655; SSN 123-45-6789; acct "
        "123456789012; 1400 Smith Street.\nvince kaminski says hi.\n\nThanks,\nMike\n"
    )
    text = t.text
    assert (
        f"{{{{p:{marta}:f}}}}," in text and f"{{{{p:{marta}:n}}}} and {{{{p:{marta}:n}}}}" in text
    )
    assert f"Mr. {{{{p:{marta}:l}}}}" in text and f"{{{{p:{marta}:e}}}}" in text
    assert "Mark the date" in text and f"{{{{p:{marta}:f}}}}'s file" in text
    assert "Corvane North America" in text and "CorvaneTrade" in text
    assert "intranet.corvane.example.test" in text and ".example.test" in text
    assert "(555) 555-01" in text and "555-01" in text and "x55" in text and "000-00-0000" in text
    assert "123456789012" not in text and "100 Commerce Street" in text
    assert "kaminski" not in text.lower() and "Mike" not in text and "{{q:" in text
    assert f"p:{marta}" in t.refs and refs_in(text) == t.refs
    assert "{ {" in p.apply("odd {{ braces }}").text


def test_titles_lower_greetings_and_unknown_nouns():
    p = _pseudo(LOTUS_CHAIN + OUTLOOK_CHAIN)
    text = p.apply("Dear Dr. Zanzibarre,\nMr. Osgood Brannick met Ms. Pellowe.\nhi marta, ok").text
    assert "Zanzibarre" not in text and "Brannick" not in text and "marta" not in text
    assert "{{s:" in text
    new = p.apply("We met Wilhelmina Zorbatek and Jerry Fanshawe today.").text
    assert "Zorbatek" not in new and "Fanshawe" not in new
    assert p.dynamic  # registered on the fly
    leftover = unknown_proper_nouns("The Quixotica with {{p:1:n}} on Monday. The NYSE.", p.reg)
    assert leftover == ["Quixotica"]
    assert p.parties("", set()) == []
    assert p.parties("Pellowe, Marta; someone@else.org", set())[1].startswith("x:")


def test_renderer_binding_and_identities():
    real = frozenset({"adrian abernathy"})
    ident = fake_identity(1, "p", 3, real=real, reserved=frozenset(), internal=True, org=None)
    assert ident.email.endswith("@corvane.example.test") and ident.name.lower() not in real
    ext = fake_identity(1, "x", 3, real=real, reserved=frozenset(), internal=False, org=2)
    assert ext.email.endswith(f"@{N.FAKE_ORGS[2]}.example.test")
    r = Renderer(1, {3: {"internal": True, "org": None}}, {4: {"internal": False, "org": 1}})
    tmpl = "{{p:3:n}} <{{p:3:e}}> / {{p:3:f}} {{p:3:l}} / {{x:4:e}} / {{q:0:f}}"
    out = r.render(tmpl)
    assert not PLACEHOLDER.search(out) and out == r.render(tmpl)
    bound = r.render(tmpl, {"p:3": ident})
    assert bound.startswith(f"{ident.name} <{ident.email}>")
    for first, last in ((n.split()[0], n.split()[-1]) for n in N.KNOWN_PEOPLE):
        assert not (first in N.FAKE_FIRST and last in N.FAKE_LAST)


def test_scanner_detects_every_kind():
    s = Scanner(extra_names=["Marta Pellowe", "single"])
    text = (
        "Enron\nJeff Skilling and Fastow\nMr. Lay\nMarta Pellowe\nVince J Kaminski\n"
        "a/HOU/ECT@ECT\n(713) 853-1234\n123-45-6789\nx@gmail.com\nwww.enron-news.com\n"
        "ok: (555) 555-0101 x5501 000-00-0000 a@b.example.test https://www.x.example.test/\n"
    )
    kinds = {f.kind for f in s.scan_text(text, "t")}
    assert kinds == {"enron", "real-name", "real-surname", "lotus-notes", "phone", "ssn",
                     "email-domain", "domain", "enron-acronym"}  # fmt: skip
    assert s.scan_text(text.splitlines()[-1]) == []
    assert str(s.scan_text("Enron")[0]).startswith("<text>:1: enron")


def test_lowercase_names_ips_rooms_and_spaced_addresses():
    p = _pseudo(LOTUS_CHAIN + "Kitchen Louise/HOU/ECT@ECT\n" + "the ask is in. " * 3)
    t = p.apply(
        "i got this from louise and pellowe today; ask nick.\nIP 10.1.2.3, room EB 3801a, "
        "call 3-3848. mail mrichter @us.ibm.com now. talk to will.\n"
    ).text
    assert "louise" not in t and "pellowe" not in t and "nick" not in t and "talk to will" in t
    assert "10.1.2.3" not in t and "192.0.2." in t and "CT 3801a" in t and "555-01" in t
    assert "ibm" not in t and "{{x:" in t
    s = Scanner()
    kinds = {f.kind for f in s.scan_text("ask lavorato\nip 10.0.0.1\nmrichter @us.ibm.com")}
    assert kinds == {"real-surname", "ip-address", "domain"}


def test_contractions_are_never_names():
    p = _pseudo(LOTUS_CHAIN + "Ive Morgan/HOU/ECT@ECT\nI've Got/HOU/ECT@ECT\n")
    t = p.apply("I'm in Chicago. I've forwarded it, I'll call. Don't worry. O'Brien agreed.").text
    assert "I'm in Chicago. I've forwarded it, I'll call. Don't worry." in t


def test_scanner_flags_address_fragments():
    kinds = [f.kind for f in Scanner().scan_text("<jdoe@Corp-\nok a@b.example.test")]
    assert kinds == ["email-domain"]


def test_phone_forms_ids_and_zips_are_replaced_and_scanned():
    p = _pseudo(LOTUS_CHAIN)
    raw = (
        "Call 555/321-4567 or 555/321-4568, London 44-201-555-0000, ext 12-3456 or 1-2345.\n"
        "Employee P00123456 / AB12345678 / member EG123456.\n100 Main St, Riverton, Texas 77002-1234; TX 77010.\n"
    )
    t = p.apply(raw).text
    for real in ("321-4567", "321-4568", "44-201-555-0000", "12-3456", "1-2345", "P00123456",
                 "AB12345678", "EG123456", "77002", "77010"):  # fmt: skip
        assert real not in t, real
    assert "CV-" in t and "79999" in t
    s = Scanner()
    kinds = {f.kind for f in s.scan_text(raw)}
    assert {"phone", "personnel-id", "zip"} <= kinds
    assert s.scan_text(t) == []
    assert {f.kind for f in s.scan_text("EPMI and ENE desks")} == {"enron-acronym"}
    assert "EPMI" not in p.apply("the EPMI desk").text


def test_mangled_names_and_unknown_organisations():
    p = _pseudo(LOTUS_CHAIN + "the media group and the bank. " * 3)
    assert "G_nther" in unknown_proper_nouns("Thanks G_nther and Fran?ois", p.reg)
    from mailsim.importers.pseudonymize import unknown_organisations

    found = unknown_organisations(
        "Quixly Media sent it; the ERCOT Group and The Bank agreed.", p.reg
    )
    assert found == ["Quixly Media"]


def test_scanner_token_pairs_match_initials_and_case():
    s = Scanner(extra_names=["Wendell Quarrick"])
    assert [f.match for f in s.scan_text("met WENDELL Q. QUARRICK and wendell quarrick")] == [
        "WENDELL QUARRICK", "wendell quarrick"]  # fmt: skip
    assert s.scan_text("Wendell spoke to Quarrick") == []


def test_postal_details_are_scrubbed():
    p = _pseudo(LOTUS_CHAIN)
    raw = "Suite 2, Memphis, TN 38120. Ship to 20171-4600, P.O. Box 748, 1181 Gothic Corridor.\n"
    t = p.apply(raw).text
    assert "38120" not in t and "20171" not in t and "748" not in t and "Gothic" not in t
    assert "TN 79999" in t and "79999-0000" in t and "P.O. Box 100" in t
    assert {f.kind for f in Scanner().scan_text(raw)} >= {"zip"}
    assert Scanner().scan_text(t) == []


def test_hashed_denylist(monkeypatch):
    # a made-up name, present only as its sha256
    monkeypatch.setattr(
        N,
        "HASHED_DENY",
        N.HASHED_DENY | {"43c05774aa2ee4b626c7b4989a16312e987898c1ce6a9dfa31a2baf07b6a3d29"},
    )
    p = _pseudo(LOTUS_CHAIN)
    t = p.apply("I am asking Quorvexa to send it.").text
    assert "Quorvexa" not in t and "{{q:" in t
    assert [f.kind for f in Scanner().scan_text("asking Quorvexa today")] == ["denylisted-name"]
    assert "Quorvexa" in unknown_proper_nouns("asking Quorvexa", p.reg)
