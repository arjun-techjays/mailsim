"""Synthetic Enron-style corpus for importer tests (no network, no real dataset).

The people below are invented; they are written in the corpus' formats (Lotus Notes
addresses, first.last@enron.com, Outlook "Last, First" headers, phone numbers, URLs) so the
pipeline exercises every detector. A tiny parquet file with a single ``text`` column mirrors
the Hugging Face shards.
"""

from __future__ import annotations

import random
from pathlib import Path

import pytest

TEST_KEY = b"test-only-identifier-key-0123456789"  # never a real key: tests only

PEOPLE = [
    ("Wendell", "Quarrick"), ("Marta", "Pellowe"), ("Osgood", "Brannick"), ("Livia", "Tazewell"),
    ("Ferris", "Ockenden"), ("Halvard", "Mirabeau"), ("Ingrid", "Sorrowby"), ("Teodor", "Vasquell"),
    ("Annika", "Thorbeck"), ("Rufus", "Grendall"), ("Selma", "Ravensworth"), ("Bastian", "Hollowell"),
]  # fmt: skip

TOPIC_LINES = {
    "finance": [
        "the budget for the quarter needs review before the books close.",
        "the invoice payment is due friday and the audit team wants the forecast.",
        "cash and revenue numbers for the quarterly earnings look fine.",
    ],
    "legal": [
        "counsel reviewed the master agreement and the indemnity clause.",
        "the litigation settlement needs legal sign off from our attorneys.",
        "regulatory compliance wants the amendment to the confidentiality agreement.",
    ],
    "board": [
        "the board meeting agenda for the board of directors is attached.",
        "the audit committee and the shareholders will see the governance update.",
        "the chairman wants the board resolution before the annual meeting.",
    ],
    "deals": [
        "the term sheet for the acquisition is ready for due diligence.",
        "we are negotiating the merger and the valuation with the counterparty.",
        "the joint venture deal needs a letter of intent this week.",
    ],
    "hr": [
        "the candidate resume looks strong and we should schedule an interview.",
        "recruiting wants the offer letter and the salary band for the hire.",
        "the analyst program candidates need a performance review slot.",
    ],
    "vendor": [
        "the vendor sent a quote for the software licence renewal.",
        "procurement needs the purchase order for the supplier contract.",
        "the service agreement with the contractor covers maintenance.",
    ],
    "scheduling": [
        "can we reschedule the conference call to thursday at 10:30 am.",
        "please put the lunch on my calendar and check the availability.",
        "the appointment and the travel itinerary are on the schedule.",
    ],
    "ops": [
        "the pipeline capacity and the gas nominations changed overnight.",
        "the server migration caused an outage on the trading desk.",
        "operations needs the curve model updated for the plant.",
    ],
    "personal": [
        "the kids loved the birthday party at the weekend.",
        "we are planning a family vacation and a round of golf.",
        "congratulations on the wedding, the baby photos are lovely.",
    ],
    "newsletter": [
        "this week in the newsletter edition: market news and a press release.",
        "click here to unsubscribe from this mailing list digest.",
        "subscribe to the weekly edition for more news.",
    ],
}
FILLER = (
    "Please review the account figures and let me know. Thanks for the help, the numbers look "
    "fine for the close and the team agreed. Please call me if you have questions. The following "
    "items are attached."
)


def _addr(p: tuple[str, str]) -> str:
    return f"{p[0]} {p[1]}/HOU/ECT@ECT"


def make_texts(n: int = 240, seed: int = 3) -> list[str]:
    rnd = random.Random(seed)
    topics = sorted(TOPIC_LINES)
    out: list[str] = []
    for i in range(n):
        topic = topics[i % len(topics)]
        a, b = rnd.sample(PEOPLE, 2)
        line = " ".join(t[:1].upper() + t[1:] for t in rnd.sample(TOPIC_LINES[topic], 2))
        body = f"{line}\n{FILLER}"
        kind = i % 4
        if kind == 0:  # single message, greeting + sign-off
            text = f"{b[0]},\n\n{body}\n\nThanks,\n{a[0]}\n"
        elif kind == 1:  # Lotus reply chain
            text = (
                f"{a[0]},\n\nAgreed. {FILLER}\n\n{b[0]}\n\n\n\n\n{a[0]} {a[1]}\n"
                f"10/02/2000 09:15 AM\nTo: {_addr(b)}\ncc: \nSubject: {topic} update\n\n"
                f"{b[0]},\n\n{body}\n\n{a[0]}\n"
            )
        elif kind == 2:  # Outlook reply chain
            text = (
                f"{a[0]} -\n\n{FILLER}\n\n{b[0]}\n\n -----Original Message-----\n"
                f"From: \t{a[1]}, {a[0]}  \nSent:\tMonday, October 02, 2000 9:15 AM\n"
                f"To:\t{b[1]}, {b[0]}\nSubject:\tRE: {topic} review\n\n{b[0]},\n\n{body}\n\n{a[0]}\n"
            )
        else:  # Lotus forward with an original
            text = (
                f"FYI\n\n{b[0]}\n---------------------- Forwarded by {b[0]} {b[1]}/HOU/ECT on "
                f"10/02/2000 09:15 AM ---------------------------\n\n\n{a[0]} {a[1]}@ENRON\n"
                f"10/01/2000 05:00 PM\nTo: {_addr(b)}\ncc: \nSubject: {topic} notes\n\n{body}\n\n"
                f"{a[0]}\n"
            )
        out.append(text)
    # identifiers every detector must catch
    out.append(
        "Wendell,\n\nCall me at (713) 853-1234 or x34567, or mail wendell.quarrick@enron.com. "
        "Details are on http://www.enron.com/docs/plan.html and at hello.example.com. "
        "Enron North America moved to 1400 Smith Street, Houston. SSN 123-45-6789, "
        "account 123456789012. Mr. Mirabeau and Dr. Sorrowby agree; Livia's note too.\n\n"
        "Marta\n"
    )
    out.append("x" * 30)  # too short for the default min_chars? (30 < 40)
    out.append("&&&& #### $$$$ %%%% ^^^^ **** (((( )))) ____ ++++ ==== ~~~~ |||| " * 3)
    return out


def write_parquet(path: Path, texts: list[str]) -> Path:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"text": texts}), str(path), row_group_size=64)
    return path


@pytest.fixture(scope="session")
def shards(tmp_path_factory) -> list[Path]:
    root = tmp_path_factory.mktemp("enron-shards")
    texts = make_texts()
    half = len(texts) // 2
    return [write_parquet(root / "0.parquet", texts[:half]),
            write_parquet(root / "1.parquet", texts[half:])]  # fmt: skip
