from mailsim.personas.prompt import (
    MAX_BODY_CHARS,
    build_persona_request,
    persona_system_prompt,
    thread_transcript,
)

PERSONA = {"id": "vendor", "role": "Vendor", "email": "vendor@example.test", "knowledge_scope": []}


def test_transcript_html_only_truncation_and_plain_addresses():
    thread = [
        {"from": {"name": "Fin", "email": "finance@example.test"}, "to": ["raw@example.test"],
         "subject": "S", "created_at": "2026-10-15T09:00:00Z", "body_html": "<p>Hello <b>you</b></p>"},
        {"from": {"email": "VENDOR@example.test"}, "to": [], "subject": "Re: S",
         "created_at": "2026-10-15T09:01:00Z", "body_text": "x" * (MAX_BODY_CHARS + 5)},
    ]  # fmt: skip
    text = thread_transcript(thread, "vendor@example.test")
    assert "From: Fin <finance@example.test>" in text and "To: raw@example.test" in text
    assert "Hello  you" in text and "<b>" not in text
    assert "--- your message ---" in text and text.endswith("[truncated]")


def test_no_knowledge_and_default_style():
    system = persona_system_prompt(PERSONA, None)
    assert "(none beyond this email thread)" in system and "neutral and brief" in system
    req = build_persona_request(PERSONA, None, [], "run_1")
    assert req.purpose == "persona" and req.run_id == "run_1" and req.max_tokens == 4096
