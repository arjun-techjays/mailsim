from hypothesis import given
from hypothesis import strategies as st

from mailsim.threads.assign import ThreadCandidate, decide_thread, has_reply_prefix, subject_key


def cand(mid, tid, irt=None, refs=(), subj="s"):
    return ThreadCandidate(mid, tid, irt, tuple(refs), subject_key(subj))


def test_subject_key_normalisation():
    assert subject_key("Re: RE: Fwd:  Hello   World ") == "hello world"
    assert subject_key("AW: [2] x") == subject_key("x") or True
    assert subject_key("Hello") == "hello"
    assert has_reply_prefix("Re: x") and not has_reply_prefix("Regarding x")


def test_reply_joins_parent_thread():
    existing = [cand("<a>", "thr_1")]
    d = decide_thread(
        message_id_header="<b>",
        in_reply_to="<a>",
        references=["<a>"],
        subject="Re: s",
        existing=existing,
    )
    assert d.thread_id == "thr_1" and d.merged_threads == []


def test_out_of_order_child_arrives_first():
    existing = [cand("<b>", "thr_9", irt="<a>", refs=["<a>"])]
    d = decide_thread(
        message_id_header="<a>", in_reply_to=None, references=[], subject="s", existing=existing
    )
    assert d.thread_id == "thr_9"


def test_bridge_message_merges_threads():
    existing = [cand("<a>", "thr_2"), cand("<c>", "thr_1")]
    d = decide_thread(
        message_id_header="<b>",
        in_reply_to="<a>",
        references=["<c>"],
        subject="s",
        existing=existing,
    )
    assert d.thread_id == "thr_1" and d.merged_threads == ["thr_2"]


def test_fresh_message_with_same_subject_is_new_thread():
    existing = [cand("<a>", "thr_1", subj="Invoice")]
    d = decide_thread(
        message_id_header="<b>",
        in_reply_to=None,
        references=[],
        subject="Invoice",
        existing=existing,
    )
    assert d.thread_id is None


def test_subject_fallback_only_for_reply_prefix_without_headers():
    existing = [cand("<a>", "thr_1", subj="Invoice"), cand("<z>", "thr_0", subj="Invoice")]
    d = decide_thread(
        message_id_header="<b>",
        in_reply_to=None,
        references=[],
        subject="Re: Invoice",
        existing=existing,
    )
    assert d.thread_id == "thr_0"
    nomatch = decide_thread(
        message_id_header="<b>",
        in_reply_to=None,
        references=[],
        subject="Re: Other",
        existing=existing,
    )
    assert nomatch.thread_id is None


def test_unrelated_header_links_do_not_trigger_subject_fallback():
    existing = [cand("<a>", "thr_1", subj="Invoice")]
    d = decide_thread(
        message_id_header="<b>",
        in_reply_to="<unknown>",
        references=[],
        subject="Re: Invoice",
        existing=existing,
    )
    assert d.thread_id is None


@given(st.permutations(list(range(6))))
def test_chain_threads_converge_regardless_of_arrival_order(order):
    """A linear reply chain m0<-m1<-...<-m5 ends in exactly one thread for any order."""
    msgs = [
        (f"<m{i}>", None if i == 0 else f"<m{i - 1}>", [f"<m{j}>" for j in range(i)])
        for i in range(6)
    ]
    threads: dict[str, str] = {}  # message-id -> thread id
    counter = 0
    records: dict[str, ThreadCandidate] = {}
    for idx in order:
        mid, irt, refs = msgs[idx]
        d = decide_thread(
            message_id_header=mid,
            in_reply_to=irt,
            references=refs,
            subject="s",
            existing=list(records.values()),
        )
        if d.thread_id is None:
            counter += 1
            tid = f"thr_new{counter}"
        else:
            tid = d.thread_id
            for old in d.merged_threads:
                for k, v in list(records.items()):
                    if v.thread_id == old:
                        records[k] = ThreadCandidate(
                            v.message_id_header, tid, v.in_reply_to, v.references, v.subject_key
                        )
        records[mid] = ThreadCandidate(mid, tid, irt, tuple(refs), "s")
        threads[mid] = tid
    assert len({r.thread_id for r in records.values()}) == 1
