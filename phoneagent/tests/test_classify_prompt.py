"""The crawl's stability classifier lives here, not in phonecli/prompts.py.

The port keeps the dynamic half of a screen as tags instead of dropping it,
so its classifier asks a different question than the repo's CLI does. The
prompt therefore belongs to phoneagent, and phonecli/prompts.py stays
untouched.
"""


def test_classification_uses_our_prompt_and_tags_instead_of_dropping(monkeypatch):
    import phonecli.llm_client as llm_client

    from phoneagent import build_map as crawler

    seen = {}

    def fake_completion(system_prompt, user_prompt, **kwargs):
        seen["system"] = system_prompt
        seen["user"] = user_prompt
        return "STABLE|Home\nDYNAMIC|某条笔记的标题"

    monkeypatch.setattr(llm_client, "text_completion", fake_completion)

    elements = [{"text": "Home"}, {"text": "某条笔记的标题"}]
    tagged = crawler._tag_dynamic(None, elements, "小红书", "", "", "")

    assert "will this exact text still be here next time" in seen["system"]
    assert [e["dynamic"] for e in tagged] == [False, True]
    # nothing is dropped: the feed still has something to tap
    assert [e["text"] for e in tagged] == ["Home", "某条笔记的标题"]


def test_the_repo_prompt_is_not_modified():
    from phoneagent.core import prompts as ours
    from phonecli import prompts as theirs

    assert "will this exact text still be here next time" in ours.ELEMENT_CLASSIFY_PROMPT
    assert "will this exact text still be here next time" not in theirs.ELEMENT_CLASSIFY_PROMPT
