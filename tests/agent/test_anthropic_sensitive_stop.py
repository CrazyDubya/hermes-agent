"""Anthropic stop_reason=sensitive is a content-policy refusal, not a generic stop.

Salvaged from fix/anthropic-whatsapp. The WhatsApp fromMe echo filter on that
tip is already on main; only the stop-reason mapping was still missing.
"""

from types import SimpleNamespace

from agent.transports.anthropic import AnthropicTransport


def test_sensitive_stop_reason_maps_to_content_filter_and_accepts_empty_content():
    transport = AnthropicTransport()
    response = SimpleNamespace(content=[], stop_reason="sensitive")
    assert transport.validate_response(response) is True
    assert transport.response_finish_reason(response) == "content_filter"


def test_unknown_stop_reason_still_maps_to_stop():
    transport = AnthropicTransport()
    response = SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="pause_turn")
    assert transport.response_finish_reason(response) == "stop"
