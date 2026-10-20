"""Generator edge cases on hand-made corpus records (templates), and pseudonymizer branches."""

from __future__ import annotations

import email
import re
from datetime import UTC, datetime
from email import policy

import pytest

from mailsim.importers import leadership
from mailsim.importers.curation import derive_subject
from mailsim.importers.leadership import GenerateOptions, Generator
from mailsim.importers.pseudonymize import Harvester, Pseudonymizer, external_meta, org_index
from tests.importers.conftest import TEST_KEY

BODY = "the budget plan and the invoice payment are attached for review this week."


def seg(**kw):
    base = {"author": None, "to": [], "cc": [], "subject": "", "kind": None, "body": BODY,
            "greeting": None, "signoff": None}  # fmt: skip
    base.update(kw)
    return base


def rec(rid, *segments, topic="finance"):
    return {"id": rid, "topic": topic, "unknown_proper": 0, "unknown_orgs": 0, "leftover_headers": False,
            "segments": list(segments)}  # fmt: skip


HEADER = {"people": {"1": {"internal": True, "org": None}, "2": {"internal": False, "org": 3}},
          "externals": {}}  # fmt: skip


def _gen(records, volume=3):
    opts = GenerateOptions(seed=1, roles=["coo"], volume={"coo": volume})
    return Generator(HEADER, records, opts)


def test_forward_embeds_original_and_fyi():
    records = [
        rec(
            "r1",
            seg(
                author="p:1",
                to=["p:2"],
                subject="plan notes",
                kind="forward",
                forwarded_by="p:2",
                body=f"{{{{p:2:f}}}},\n\n{BODY}\n\n{{{{p:1:f}}}}",
                greeting="p:2",
                signoff="p:1",
            ),
            seg(body=""),
        ),  # fmt: skip
    ]
    sc = _gen(records, volume=1).generate_role("coo")
    assert sc.messages == 1
    raw = next(v for k, v in sc.files.items() if k.endswith(".eml"))
    m = email.message_from_bytes(raw, policy=policy.default)
    text = m.get_body(("plain",)).get_content()
    assert m["Subject"] == "FW: plan notes" and text.startswith("FYI.")
    assert "---------- Forwarded message ----------" in text and "From: " in text


def test_unbindable_pii_and_fallbacks():
    records = [
        # embedded original without a sender
        rec("r1", seg(subject="a", kind="forward"), seg()),
        # the greeting names someone other than the addressee in one segment, the author in
        # the next: contradictory binding
        rec(
            "r2",
            seg(author="p:1", subject="b", kind="reply", greeting="p:2"),
            seg(greeting="p:2", signoff="p:2"),
            seg(signoff="p:2"),
        ),  # fmt: skip
        # thread longer than the remaining quota
        rec(
            "r4",
            seg(author="p:1", subject="c", kind="reply"),
            seg(author="p:2", kind="reply"),
            seg(),
        ),  # fmt: skip
        # fine: no sign-off -> the sender's first name is appended; subject from the body
        rec("r5", seg()),
        rec("r6", seg(body=BODY + " also the forecast."), topic="general"),
    ]
    gen = _gen(records, volume=2)
    sc = gen.generate_role("coo")
    assert sc.skipped["unbindable"] >= 1
    assert sc.messages == 2  # r5 + general fallback r6
    msgs = [email.message_from_bytes(v, policy=policy.default) for k, v in sc.files.items()
            if k.endswith(".eml")]  # fmt: skip
    texts = [m.get_body(("plain",)).get_content() for m in msgs]
    assert any(t.rstrip().endswith(("Graham", "Marcus", "Priya")) for t in texts)
    # the bodies say the plan is attached: each message carries a generated document
    assert all(any(p.get_filename() for p in m.iter_attachments()) for m in msgs)


def test_residual_identifier_is_skipped():
    # a residual identifier in the rendered text: skipped by the scanner
    body = f"{BODY}\nCall me on 713-853-1234."
    sc = _gen([rec("r3", seg(body=body), topic="ops")], volume=1).generate_role("coo")
    assert sc.messages == 0 and sc.skipped["pii"] >= 1


def test_derive_subject_and_quotas():
    assert derive_subject("ok\n\nthanks", "finance") == "Finance follow-up"
    long = "Attached is the revised budget for the plant, which we discussed. More later."
    assert derive_subject(long) == "The revised budget for the plant"
    assert derive_subject("Weekly Gas Report\n\nNumbers are below.") == "Weekly Gas Report"
    assert sum(leadership._quotas({"a": 1, "b": 1, "c": 1}, 10).values()) == 10


def test_pseudonymizer_branches():
    h = Harvester()
    h.feed('"Quarrick, Wendell" <wq@partner.org>\nTo: Pellowe, Marta; x@y.org\n'
           "From: Wendell Quarrick <wendell.quarrick@partner.org>\n"
           "ann.brannick@gmail.com and ann.brannick@yahoo.com\n")  # fmt: skip
    reg = h.finish()
    p = Pseudonymizer(reg, 3, TEST_KEY)
    wendell = reg.lookup("Wendell", "Quarrick")
    assert wendell is not None and reg.people[wendell].domain == "partner.org"
    ann = reg.lookup("Ann", "Brannick")
    assert reg.people[ann].domain == "gmail.com"  # first external domain wins
    t = p.apply('"Quarrick, Wendell" <wq@partner.org> wrote to "Nobody Here" <n@h.org>; '
                "see host.enron.com and www.partner.org. The Quarricks' plan; Quarrick agreed. "
                "Ok.\n\nmike\n").text  # fmt: skip
    assert f"{{{{p:{wendell}:n}}}} <{{{{p:{wendell}:e}}}}>" in t
    assert '"Nobody Here" <{{x:' in t and "corvane.example.test" in t
    assert f"{{{{p:{wendell}:l}}}} agreed" in t and "{{q:" in t
    assert org_index(3, None) is None and org_index(3, "a.org") == org_index(3, "a.org")
    p.apply("mail me at zed@enron.com")
    meta = external_meta(p, range(len(p.email_domains)))
    assert any(v["internal"] for v in meta.values())


def test_days_validation():
    import pytest

    with pytest.raises(ValueError, match="days"):
        Generator(HEADER, [], GenerateOptions(days=1))


def test_pseudonyms_never_collide_within_a_scenario():
    from mailsim.importers.pseudonymize import Renderer

    r = Renderer(1)
    r.new_scope(["Helena Marsh"])
    names = [r.identity(f"p:{i}").name for i in range(400)]
    assert len(set(names)) == len(names) and "Helena Marsh" not in names
    assert r.identity("p:3") is r.identity("p:3")


def test_directory_listings_are_rejected():
    listing = "{{p:1:n}} 1 34299 4120J 3971\n{{p:2:n}} 2 36403 4120G 3972"
    sc = _gen([rec("r9", seg(body=f"{BODY}\n{listing}"))], volume=1).generate_role("coo")
    assert sc.messages == 0


def test_identical_bodies_are_used_once():
    sc = _gen([rec("r1", seg()), rec("r2", seg())], volume=2).generate_role("coo")
    assert sc.messages == 1 and sc.skipped.get("duplicate") == 1


def _bodies(sc):
    msgs = [email.message_from_bytes(v, policy=policy.default) for k, v in sc.files.items()
            if k.endswith(".eml")]  # fmt: skip
    return msgs, [m.get_body(("plain",)).get_content() for m in msgs]


def _skips(record, role="ceo"):
    risks = len(leadership.RISKS.get(role, ()))
    opts = GenerateOptions(seed=1, roles=[role], volume={role: risks + 2})
    sc = Generator(HEADER, [record], opts).generate_role(role)
    return sc.messages - risks, {k: v for k, v in sc.skipped.items() if v}


FWD = dict(author="p:1", subject="plan", kind="forward", body=BODY)


@pytest.mark.parametrize(
    ("record", "reason"),
    [
        # the personal contact may not forward work mail
        (rec("r1", seg(**FWD), seg(body="Have a look at this when you can, thanks a lot."),
             topic="personal"), "incoherent"),
        # a year the shift cannot place
        (rec("r2", seg(body=BODY + " The term runs through 2011 for the plant.")), "dates"),
        # a recipient named in the third person in the body
        (rec("r3", seg(body="Hi,\n\nPlease check this with Marsh and the budget team today. "
                            "Thanks.")),
         "incoherent"),
        # dates no shift can place: the future-tense one lies before the past-tense one
        (rec("r4", seg(body="The budget review was held on Friday, May 4, 2001. The plant plan "
                            "will be signed on Tuesday, May 1, 2001.")), "dates"),
        # a year-less date without any tense marker cannot be placed
        (rec("r5", seg(body="The budget plan is fine. The review is a couple of weeks from "
                            "June 1 for the budget.")), "dates"),
        # back-office deal matching sent to the CEO
        (rec("r6", seg(body="Please match the following deals for the plant budget: deal 172426 "
                            "with 132316. Thanks.")), "incoherent"),
        # a reply bound to the sender of the message it answers
        (rec("r7", seg(author="p:1", subject="plan", kind="reply", body=BODY),
             seg(author="p:1", body="And the forecast for the plant is attached as well.")),
         "unbindable"),
    ],
    ids=["personal-forward", "stale-year", "third-person-recipient", "future-before-send",
         "yearless-no-tense", "back-office-to-ceo", "reply-to-self"],
)  # fmt: skip
def test_generator_skips_incoherent_and_undatable_records(record, reason):
    messages, skipped = _skips(record)
    assert messages == 0 and skipped == {reason: 1}


def test_stub_only_messages_get_a_lead_line_and_a_spreadsheet():
    body = "{{q:3:f}},\n\n - storage proxies.xls\n\nthe budget plan for the plant is in the file."
    sc = _gen([rec("r1", seg(body=body))], volume=1).generate_role("coo")
    msgs, texts = _bodies(sc)
    assert sc.messages == 1
    names = [a.get_filename() for a in msgs[0].iter_attachments()]
    assert names == ["Storage-Proxies.xlsx"] and "storage proxies.xls" not in texts[0]


def test_forward_after_a_reply_dates_the_original_between_them():
    records = [
        rec(
            "r1",
            seg(author="p:1", to=["p:2"], subject="plan notes", kind="reply", body=BODY),
            seg(author="p:2", to=["p:1"], kind="forward", body="Agreed, see below. " + BODY),
            seg(body="FYI, the forecast is below for review this week."),
        ),
    ]  # fmt: skip
    sc = _gen(records, volume=3).generate_role("coo")
    msgs, texts = _bodies(sc)
    forwards = [t for t in texts if "Forwarded message" in t]
    assert sc.messages == 2 and len(forwards) == 1
    reply, forward = sorted(msgs, key=lambda m: m["Date"].datetime)
    inner = re.search(r"(?m)^Date: (.+)$", forwards[0])
    assert inner is not None
    original = datetime.strptime(inner.group(1).strip(), "%a, %d %b %Y %H:%M").replace(tzinfo=UTC)
    assert reply["Date"].datetime < original < forward["Date"].datetime
