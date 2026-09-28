import json

from bot.config import BotConfig
from bot.report_writer import (
    ChatCompletionsReportWriter,
    _responses_text,
    _writer_protocol,
    build_report_writer,
)
from runtime.research_providers import ResearchConfig


def test_opencode_go_defaults_split_model_roles(monkeypatch, tmp_path):
    monkeypatch.setenv("OGASAWARA_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-test-key")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("ANALYST_WRITER_MODEL", raising=False)
    monkeypatch.delenv("VALIDATOR_MODEL", raising=False)
    monkeypatch.delenv("RESEARCH_LLM_MODEL", raising=False)
    monkeypatch.delenv("RESEARCH_PLANNER_MODEL", raising=False)

    bot = BotConfig.from_env()
    research = ResearchConfig.from_env()

    assert bot.openai_api_key == "go-test-key"
    assert bot.openai_base_url == "https://opencode.ai/zen/go/v1"
    assert bot.openai_writer_model == "gpt-5.6-luna"
    assert bot.validator_model == "deepseek-v4.1-flash"
    assert bot.writer_protocol == "auto"
    assert research.base_url == "https://opencode.ai/zen/go/v1"
    assert research.planner_model == "glm-5.3"
    assert research.model == "deepseek-v4.1-flash"


def test_explicit_model_role_overrides_win(monkeypatch, tmp_path):
    monkeypatch.setenv("OGASAWARA_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "go-test-key")
    monkeypatch.setenv("ANALYST_WRITER_MODEL", "grok-4.6")
    monkeypatch.setenv("VALIDATOR_MODEL", "glm-5.3-flash")
    monkeypatch.setenv("RESEARCH_PLANNER_MODEL", "glm-5.2")
    monkeypatch.setenv("RESEARCH_LLM_MODEL", "deepseek-v4-flash")

    bot = BotConfig.from_env()
    research = ResearchConfig.from_env()

    assert bot.openai_writer_model == "grok-4.6"
    assert bot.validator_model == "glm-5.3-flash"
    assert research.planner_model == "glm-5.2"
    assert research.model == "deepseek-v4-flash"


def test_luna_routes_to_responses_and_review_models_route_to_chat():
    assert _writer_protocol("gpt-5.6-luna") == "responses"
    assert _writer_protocol("opencode-go/gpt-5.6-luna") == "responses"
    assert _writer_protocol("glm-5.3") == "chat"
    assert _writer_protocol("deepseek-v4.1-flash") == "chat"
    assert _writer_protocol("gpt-5.6-luna", "chat") == "chat"


def test_responses_text_extracts_message_blocks():
    payload = {
        "output": [{
            "type": "message",
            "content": [
                {"type": "output_text", "text": "第一段"},
                {"type": "output_text", "text": "第二段"},
            ],
        }]
    }
    assert _responses_text(payload) == "第一段\n第二段"


def test_writer_uses_responses_for_luna_and_chat_for_validator():
    writer = build_report_writer(
        api_key="go-test-key",
        model="gpt-5.6-luna",
        base_url="https://opencode.ai/zen/go/v1",
        validator_model="deepseek-v4.1-flash",
    )
    assert isinstance(writer, ChatCompletionsReportWriter)
    assert writer.protocol == "responses"
    assert writer.validator_model == "deepseek-v4.1-flash"


def test_routed_writer_calls_expected_paths_without_network():
    writer = ChatCompletionsReportWriter(
        api_key="key",
        model="gpt-5.6-luna",
        base_url="https://opencode.ai/zen/go/v1",
        validator_model="deepseek-v4.1-flash",
    )
    calls = []

    def fake_post(path, payload, headers):
        calls.append((path, payload["model"]))
        if path == "/responses":
            return {
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "writer-output"}],
                }]
            }
        return {"choices": [{"message": {"content": json.dumps({"issues": []})}}]}

    writer._post = fake_post
    headers = writer._headers("session")
    answer = writer._call_model(
        model="gpt-5.6-luna",
        protocol="responses",
        system="system",
        user="user",
        headers=headers,
        max_tokens=100,
    )
    review = writer._call_model(
        model="deepseek-v4.1-flash",
        protocol="chat",
        system="system",
        user="user",
        headers=headers,
        max_tokens=100,
    )

    assert answer == "writer-output"
    assert json.loads(review) == {"issues": []}
    assert calls == [
        ("/responses", "gpt-5.6-luna"),
        ("/chat/completions", "deepseek-v4.1-flash"),
    ]
