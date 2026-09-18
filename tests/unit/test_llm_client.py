from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
import pytest

from quantdesk.research.llm_research_client import (
    ALLOWED_PARAMETER_BOUNDS,
    LLMProvider,
    LLMProposal,
    LLMResearchClient,
)
from quantdesk.research.agentic_researcher import AgenticResearchLoop
from quantdesk.strategies.unified_agentic import UnifiedAgenticAlphaEngine


def test_llm_client_initialization_defaults():
    client = LLMResearchClient()
    status = client.get_status()
    assert status["active_provider"] in ("deepseek", "gemini", "offline")
    assert status["deepseek"]["model"] == "deepseek-flash"
    assert "gemini" in status



def test_llm_client_set_provider():
    client = LLMResearchClient()
    client.set_provider("deepseek", api_key="sk-test-deepseek-key-12345", model="deepseek-chat")
    assert client.provider == LLMProvider.DEEPSEEK
    assert client.deepseek_api_key == "sk-test-deepseek-key-12345"
    assert client.deepseek_model == "deepseek-chat"

    status = client.get_status()
    assert status["active_provider"] == "deepseek"
    assert status["deepseek"]["configured"] is True
    assert "sk-tes" in status["deepseek"]["key_preview"]

    client.set_provider("gemini", api_key="AIzaSy-fake-gemini-key-9999", model="gemini-2.0-flash")
    assert client.provider == LLMProvider.GEMINI
    assert client.gemini_api_key == "AIzaSy-fake-gemini-key-9999"
    assert client.gemini_model == "gemini-2.0-flash"

    client.set_provider("offline")
    assert client.provider == LLMProvider.OFFLINE


def test_parameter_bounds_sanitization_and_clamping():
    client = LLMResearchClient()

    # Test unknown parameter defaults to volatility_hurdle_bps
    param, val = client._sanitize_proposal("hallucinated_param", 10.0, {"volatility_hurdle_bps": 5.0})
    assert param == "volatility_hurdle_bps"
    assert 3.0 <= val <= 18.0

    # Test value below minimum allowed bound gets clamped
    min_hurdle, _ = ALLOWED_PARAMETER_BOUNDS["volatility_hurdle_bps"]
    param, val = client._sanitize_proposal("volatility_hurdle_bps", 0.1, {})
    assert val == min_hurdle

    # Test value above maximum allowed bound gets clamped
    _, max_hurdle = ALLOWED_PARAMETER_BOUNDS["volatility_hurdle_bps"]
    param, val = client._sanitize_proposal("volatility_hurdle_bps", 99.9, {})
    assert val == max_hurdle

    # Test depth5_threshold bounds
    min_d, max_d = ALLOWED_PARAMETER_BOUNDS["depth5_threshold"]
    param, val = client._sanitize_proposal("depth5_threshold", 0.05, {})
    assert val == min_d


def test_mock_deepseek_call():
    client = LLMResearchClient(provider="deepseek", deepseek_api_key="sk-test-key")
    mock_response_payload = {
        "choices": [
            {
                "message": {
                    "content": json.dumps({
                        "diagnosis": "Microstructure fee drag identified in choppy regime",
                        "target_parameter": "atr_target_mult",
                        "proposed_value": 3.2,
                        "rationale": "Widen profit targets to 3.2x ATR to absorb maker/taker fees",
                        "confidence": 0.88,
                    })
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_response_payload
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.post", return_value=mock_resp):
        proposal = client.generate_hypothesis(
            instrument_id="BTCUSDT",
            diagnosis_report={"total_trades": 10, "top_alpha_leak": "FEE_DRAG_LOSS"},
            current_parameters={"atr_target_mult": 2.5},
        )

    assert proposal.target_parameter == "atr_target_mult"
    assert proposal.proposed_value == 3.2
    assert "deepseek" in proposal.model_used
    assert "Widen profit targets" in proposal.rationale


def test_mock_gemini_call():
    client = LLMResearchClient(provider="gemini", gemini_api_key="AIzaSy-fake-key")
    mock_response_payload = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {
                            "text": json.dumps({
                                "diagnosis": "Frequent whip-saws causing rapid stop out",
                                "target_parameter": "depth5_threshold",
                                "proposed_value": 0.45,
                                "rationale": "Raise order book depth imbalance to filter out thin liquidity fakeouts",
                                "confidence": 0.92,
                            })
                        }
                    ]
                }
            }
        ]
    }

    mock_resp = MagicMock()
    mock_resp.json.return_value = mock_response_payload
    mock_resp.raise_for_status.return_value = None

    with patch("httpx.Client.post", return_value=mock_resp):
        proposal = client.generate_hypothesis(
            instrument_id="ETHUSDT",
            diagnosis_report={"total_trades": 15, "top_alpha_leak": "RAPID_STOP_CHOP"},
            current_parameters={"depth5_threshold": 0.35},
        )

    assert proposal.target_parameter == "depth5_threshold"
    assert proposal.proposed_value == 0.45
    assert "gemini" in proposal.model_used


def test_offline_fallback_on_network_error():
    client = LLMResearchClient(provider="deepseek", deepseek_api_key="sk-test-key")

    with patch("httpx.Client.post", side_effect=Exception("Connection timed out")):
        proposal = client.generate_hypothesis(
            instrument_id="BTCUSDT",
            diagnosis_report={"top_alpha_leak": "FEE_DRAG_LOSS"},
            current_parameters={"atr_target_mult": 2.5},
        )

    assert proposal is not None
    assert "offline" in proposal.model_used
    assert proposal.target_parameter == "atr_target_mult"


def test_agentic_research_loop_integration_with_llm():
    mock_client = LLMResearchClient(provider="offline")
    loop = AgenticResearchLoop(instrument_id="BTCUSDT", llm_client=mock_client)
    engine = UnifiedAgenticAlphaEngine(instrument_id="BTCUSDT")

    bars = [
        {"close": 60000.0 + i * 5.0, "high": 60010.0 + i * 5.0, "low": 59990.0 + i * 5.0, "atr14": 40.0, "ema7": 60000.0, "sma15": 59990.0}
        for i in range(50)
    ]

    hypo = loop.run_research_cycle(engine, bars)
    assert hypo is not None
    assert hypo.model_used.startswith("offline")
    assert hypo.status in ("PROMOTED", "REJECTED")

    status = loop.get_status()
    latest_hypo = status["recent_hypotheses"][0]
    assert "model_used" in latest_hypo
    assert "latency_ms" in latest_hypo
