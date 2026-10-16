"""Generator edge cases on hand-made corpus records (templates), and pseudonymizer branches."""

from __future__ import annotations

import email
from email import policy

from mailsim.importers import leadership
from mailsim.importers.leadership import GenerateOptions, Generator, _derive_subject
from mailsim.importers.pseudonymize import Harvester, Pseudonymizer, external_meta, org_index

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
        rec("r6", seg(), topic="general"),
    ]
    gen = _gen(records, volume=2)
    sc = gen.generate_role("coo")
    assert sc.skipped["unbindable"] >= 1
    assert sc.messages == 2  # r5 + general fallback r6
    texts = [v.decode() for k, v in sc.files.items() if k.endswith(".eml")]
    assert any(t.rstrip().endswith(("Graham", "Marcus", "Priya")) for t in texts)


def test_residual_identifier_is_skipped():
    # a residual identifier in the rendered text: skipped by the scanner
    sc = _gen([rec("r3", seg(body=f"{BODY} call 713-853-1234"))], volume=1).generate_role("coo")
    assert (sc.messages, sc.skipped["pii"]) == (0, 1)


def test_derive_subject_and_quotas():
    assert _derive_subject("ok\n\nthanks") == "Quick note"
    long = "one two three four five six seven eight nine ten. more"
    assert _derive_subject(long) == "one two three four five six seven eight..."
    assert sum(leadership._quotas({"a": 1, "b": 1, "c": 1}, 10).values()) == 10


def test_pseudonymizer_branches():
    h = Harvester()
    h.feed('"Quarrick, Wendell" <wq@partner.org>\nTo: Pellowe, Marta; x@y.org\n'
           "From: Wendell Quarrick <wendell.quarrick@partner.org>\n"
           "ann.brannick@gmail.com and ann.brannick@yahoo.com\n")  # fmt: skip
    reg = h.finish()
    p = Pseudonymizer(reg, 3)
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


def test_days_validation_and_year_shift():
    import pytest

    with pytest.raises(ValueError, match="days"):
        Generator(HEADER, [], GenerateOptions(days=1))
    from mailsim.importers.leadership import shift_years

    assert shift_years("2000 MW in 2001; 01/02/2002, March 4, 2000, 2000-2001", 24) == (
        "2000 MW in 2025; 01/02/2026, March 4, 2024, 2024-2025")  # fmt: skip


def test_pseudonyms_never_collide_within_a_scenario():
    from mailsim.importers.pseudonymize import Renderer

    r = Renderer(1)
    r.new_scope(["Helena Marsh"])
    names = [r.identity(f"p:{i}").name for i in range(400)]
    assert len(set(names)) == len(names) and "Helena Marsh" not in names
    assert r.identity("p:3") is r.identity("p:3")
