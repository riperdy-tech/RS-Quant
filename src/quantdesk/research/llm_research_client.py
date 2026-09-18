"""Tier 3 LLM Research Connector for DeepSeek and Google Gemini.

Powers the autonomous reasoning loop for hypothesis formulation and parameter mutation
by connecting to DeepSeek (v3/v4.1/Flash) and Google Gemini (1.5/2.0 Flash/Pro).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import json
import logging
import os
from pathlib import Path
import time
from typing import Any

from dotenv import load_dotenv
import httpx

load_dotenv()

logger = logging.getLogger("quantdesk.llm_research_client")


def _persist_to_env(key: str, value: str) -> None:
    """Safely writes or updates configuration parameter in .env file."""
    try:
        env_path = Path(".env")
        if not env_path.exists():
            env_path.write_text(f"{key}={value}\n", encoding="utf-8")
            return

        lines = env_path.read_text(encoding="utf-8").splitlines()
        found = False
        new_lines = []
        for line in lines:
            if line.strip().startswith(f"{key}="):
                new_lines.append(f"{key}={value}")
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f"{key}={value}")

        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except Exception as e:
        logger.warning("Could not persist %s to .env: %s", key, e)


# Strict institutional parameter bounds to prevent LLM hallucination
ALLOWED_PARAMETER_BOUNDS: dict[str, tuple[float, float]] = {
    "volatility_hurdle_bps": (3.0, 18.0),
    "depth5_threshold": (0.20, 0.65),
    "atr_target_mult": (1.5, 4.5),
    "conviction_threshold": (0.25, 0.60),
    "entry_cooldown_s": (15.0, 300.0),
}


class LLMProvider(StrEnum):
    DEEPSEEK = "deepseek"
    GEMINI = "gemini"
    OFFLINE = "offline"


@dataclass
class LLMProposal:
    target_parameter: str
    proposed_value: float
    rationale: str
    diagnosis: str
    model_used: str
    latency_ms: int
    raw_response: dict[str, Any] | None = None


class LLMResearchClient:
    """Manages AI provider selection, prompt formatting, and structured hypothesis generation."""

    def __init__(
        self,
        provider: str | None = None,
        deepseek_api_key: str | None = None,
        deepseek_model: str = "deepseek-flash",
        gemini_api_key: str | None = None,
        gemini_model: str = "gemini-1.5-flash",
    ) -> None:
        self.deepseek_api_key = deepseek_api_key or os.getenv("DEEPSEEK_API_KEY", "").strip()
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL", deepseek_model).strip()

        self.gemini_api_key = gemini_api_key or os.getenv("GEMINI_API_KEY", "").strip()
        self.gemini_model = os.getenv("GEMINI_MODEL", gemini_model).strip()

        # Automatic provider detection
        configured_prov = (provider or os.getenv("AI_RESEARCH_PROVIDER", "")).strip().lower()
        if configured_prov in ("deepseek", "gemini", "offline"):
            self.provider = LLMProvider(configured_prov)
        elif self.deepseek_api_key:
            self.provider = LLMProvider.DEEPSEEK
        elif self.gemini_api_key:
            self.provider = LLMProvider.GEMINI
        else:
            self.provider = LLMProvider.OFFLINE

    def set_provider(
        self,
        provider: str,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        """Dynamically switches active LLM provider, credentials, and model target."""
        prov = provider.strip().lower()
        if prov == "deepseek":
            self.provider = LLMProvider.DEEPSEEK
            if api_key is not None:
                self.deepseek_api_key = api_key.strip()
                os.environ["DEEPSEEK_API_KEY"] = self.deepseek_api_key
                _persist_to_env("DEEPSEEK_API_KEY", self.deepseek_api_key)
            if model:
                self.deepseek_model = model.strip()
                os.environ["DEEPSEEK_MODEL"] = self.deepseek_model
                _persist_to_env("DEEPSEEK_MODEL", self.deepseek_model)
        elif prov == "gemini":
            self.provider = LLMProvider.GEMINI
            if api_key is not None:
                self.gemini_api_key = api_key.strip()
                os.environ["GEMINI_API_KEY"] = self.gemini_api_key
                _persist_to_env("GEMINI_API_KEY", self.gemini_api_key)
            if model:
                self.gemini_model = model.strip()
                os.environ["GEMINI_MODEL"] = self.gemini_model
                _persist_to_env("GEMINI_MODEL", self.gemini_model)
        else:
            self.provider = LLMProvider.OFFLINE

        os.environ["AI_RESEARCH_PROVIDER"] = self.provider.value
        _persist_to_env("AI_RESEARCH_PROVIDER", self.provider.value)
        logger.info(
            "LLM Research Client updated: provider=%s, deepseek_configured=%s, gemini_configured=%s",
            self.provider.value,
            bool(self.deepseek_api_key),
            bool(self.gemini_api_key),
        )

    def update_config(
        self,
        provider: str,
        api_key: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Convenience method to set provider configuration and return updated status."""
        self.set_provider(provider=provider, api_key=api_key, model=model)
        return self.get_status()

    def get_status(self) -> dict[str, Any]:
        """Returns the active provider configuration (masking secrets)."""
        return {
            "active_provider": self.provider.value,
            "deepseek": {
                "configured": bool(self.deepseek_api_key),
                "model": self.deepseek_model,
                "key_preview": f"{self.deepseek_api_key[:6]}...{self.deepseek_api_key[-4:]}" if len(self.deepseek_api_key) > 10 else None,
            },
            "gemini": {
                "configured": bool(self.gemini_api_key),
                "model": self.gemini_model,
                "key_preview": f"{self.gemini_api_key[:6]}...{self.gemini_api_key[-4:]}" if len(self.gemini_api_key) > 10 else None,
            },
        }

    def generate_hypothesis(
        self,
        instrument_id: str,
        diagnosis_report: dict[str, Any],
        current_parameters: dict[str, Any],
        rolling_ic: dict[str, float] | None = None,
    ) -> LLMProposal:
        """Formulates an intelligent parameter mutation via DeepSeek, Gemini, or offline fallback."""
        start_time = time.perf_counter()

        # Check if active provider is configured with an API key
        if self.provider == LLMProvider.DEEPSEEK and self.deepseek_api_key:
            try:
                return self._call_deepseek(instrument_id, diagnosis_report, current_parameters, rolling_ic, start_time)
            except Exception as e:
                logger.error("DeepSeek API call failed: %s. Falling back to rule engine.", e)

        elif self.provider == LLMProvider.GEMINI and self.gemini_api_key:
            try:
                return self._call_gemini(instrument_id, diagnosis_report, current_parameters, rolling_ic, start_time)
            except Exception as e:
                logger.error("Gemini API call failed: %s. Falling back to rule engine.", e)

        # Fallback offline heuristic generator
        return self._offline_fallback(instrument_id, diagnosis_report, current_parameters, start_time)

    def _build_prompt(
        self,
        instrument_id: str,
        diagnosis: dict[str, Any],
        current_params: dict[str, Any],
        rolling_ic: dict[str, float] | None,
    ) -> str:
        """Constructs quantitative reasoning prompt requiring strict JSON schema output."""
        return f"""You are a Lead Quantitative Portfolio Manager optimizing an autonomous 3x leverage perpetual futures algorithm on Bitget for {instrument_id}.

### MICROSTRUCTURE LOSS POST-MORTEM REPORT:
- Total Evaluated Trades: {diagnosis.get('total_trades', 0)}
- Realized Win Rate: {diagnosis.get('win_rate_pct', 0.0)}%
- Total Net PnL: {diagnosis.get('total_net_pnl', 0.0)} USDT
- Primary Alpha Leak Tag: {diagnosis.get('top_alpha_leak', 'NONE')}
- Attribution Clusters Breakdown:
{json.dumps(diagnosis.get('clusters', {}), indent=2)}

### CURRENT STRATEGY PARAMETERS:
- volatility_hurdle_bps: {current_params.get('volatility_hurdle_bps', 5.0)} (Allowed: 3.0 to 18.0)
- depth5_threshold: {current_params.get('depth5_threshold', 0.35)} (Allowed: 0.20 to 0.65)
- atr_target_mult: {current_params.get('atr_target_mult', 2.5)} (Allowed: 1.5 to 4.5)
- conviction_threshold: {current_params.get('conviction_threshold', 0.35)} (Allowed: 0.25 to 0.60)
- entry_cooldown_s: {current_params.get('entry_cooldown_s', 45)} (Allowed: 15 to 300)

### RECENT INDICATOR INFORMATION COEFFICIENTS:
{json.dumps(rolling_ic or {}, indent=2)}

### INSTRUCTIONS:
1. Diagnose why recent trades were unprofitable (e.g. fee drag erosion vs. false breakout chop vs. premature momentum decay).
2. Select EXACTLY ONE target parameter from the current strategy parameters list to mutate.
3. Propose a reasonable, cautious new numeric value within the allowed bounds.
4. Output your decision in STRICT JSON ONLY, matching the schema below:

{{
  "diagnosis": "<Concise diagnostic root cause analysis>",
  "target_parameter": "<one of: volatility_hurdle_bps, depth5_threshold, atr_target_mult, conviction_threshold, entry_cooldown_s>",
  "proposed_value": <float or int>,
  "rationale": "<Reasoning why this specific adjustment solves the diagnosed alpha leak>",
  "confidence": <float between 0.0 and 1.0>
}}
"""

    def _call_deepseek(
        self,
        instrument_id: str,
        diagnosis: dict[str, Any],
        current_params: dict[str, Any],
        rolling_ic: dict[str, float] | None,
        start_time: float,
    ) -> LLMProposal:
        """Calls DeepSeek OpenAI-compatible chat completion endpoint."""
        prompt = self._build_prompt(instrument_id, diagnosis, current_params, rolling_ic)
        url = "https://api.deepseek.com/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a quantitative research system that outputs only valid JSON conforming strictly to the requested schema.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        latency = int((time.perf_counter() - start_time) * 1000)

        param, val = self._sanitize_proposal(parsed.get("target_parameter"), parsed.get("proposed_value"), current_params)
        return LLMProposal(
            target_parameter=param,
            proposed_value=val,
            rationale=parsed.get("rationale", "DeepSeek quantitative optimization."),
            diagnosis=parsed.get("diagnosis", "DeepSeek loss cluster analysis."),
            model_used=f"deepseek:{self.deepseek_model}",
            latency_ms=latency,
            raw_response=parsed,
        )

    def _call_gemini(
        self,
        instrument_id: str,
        diagnosis: dict[str, Any],
        current_params: dict[str, Any],
        rolling_ic: dict[str, float] | None,
        start_time: float,
    ) -> LLMProposal:
        """Calls Google Gemini REST API endpoint."""
        prompt = self._build_prompt(instrument_id, diagnosis, current_params, rolling_ic)
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.gemini_model}:generateContent?key={self.gemini_api_key}"
        headers = {"Content-Type": "application/json"}
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0.2,
                "responseMimeType": "application/json",
            },
        }

        with httpx.Client(timeout=15.0) as client:
            resp = client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        text = data["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(text)
        latency = int((time.perf_counter() - start_time) * 1000)

        param, val = self._sanitize_proposal(parsed.get("target_parameter"), parsed.get("proposed_value"), current_params)
        return LLMProposal(
            target_parameter=param,
            proposed_value=val,
            rationale=parsed.get("rationale", "Gemini quantitative optimization."),
            diagnosis=parsed.get("diagnosis", "Gemini loss cluster analysis."),
            model_used=f"gemini:{self.gemini_model}",
            latency_ms=latency,
            raw_response=parsed,
        )

    def _offline_fallback(
        self,
        instrument_id: str,
        diagnosis: dict[str, Any],
        current_params: dict[str, Any],
        start_time: float,
    ) -> LLMProposal:
        """Deterministic offline rule-based fallback when no API key is provided."""
        leak = diagnosis.get("top_alpha_leak")
        target_param = "volatility_hurdle_bps"
        base_val = float(current_params.get("volatility_hurdle_bps", 5.0))
        prop_val = round(base_val + 1.5, 2)
        rationale = "Offline rule baseline: Widening volatility hurdle to filter market noise."

        if leak == "FEE_DRAG_LOSS":
            target_param = "atr_target_mult"
            base_val = float(current_params.get("atr_target_mult", 2.5))
            prop_val = round(base_val + 0.3, 2)
            rationale = "Fee drag diagnosed: Widening profit target to overcome round-trip exchange friction."
        elif leak == "RAPID_STOP_CHOP":
            target_param = "depth5_threshold"
            base_val = float(current_params.get("depth5_threshold", 0.35))
            prop_val = round(min(0.60, base_val + 0.05), 2)
            rationale = "Chop stop-outs diagnosed: Elevating L2 order book imbalance hurdle."
        elif leak == "ALPHA_SCRATCH":
            target_param = "conviction_threshold"
            base_val = float(current_params.get("conviction_threshold", 0.35))
            prop_val = round(min(0.50, base_val + 0.05), 2)
            rationale = "Alpha scratching diagnosed: Increasing initial momentum conviction hurdle."

        latency = int((time.perf_counter() - start_time) * 1000)
        param, val = self._sanitize_proposal(target_param, prop_val, current_params)
        return LLMProposal(
            target_parameter=param,
            proposed_value=val,
            rationale=rationale,
            diagnosis=f"Rule attribution identified {leak or 'NORMAL_VARIANCE'}",
            model_used="offline:deterministic-rule-engine",
            latency_ms=latency,
        )

    def _sanitize_proposal(
        self,
        param: Any,
        val: Any,
        current_params: dict[str, Any],
    ) -> tuple[str, float]:
        """Enforces parameter whitelist and clamps within strict institutional bounds."""
        param_str = str(param or "volatility_hurdle_bps").strip()
        if param_str not in ALLOWED_PARAMETER_BOUNDS:
            param_str = "volatility_hurdle_bps"

        min_bound, max_bound = ALLOWED_PARAMETER_BOUNDS[param_str]
        try:
            val_float = float(val)
        except (TypeError, ValueError):
            val_float = float(current_params.get(param_str, min_bound))

        # Clamp within allowed bounds
        clamped = max(min_bound, min(max_bound, round(val_float, 3)))
        return param_str, clamped


# Global singleton instance for system-wide reuse and live synchronization
shared_llm_client: LLMResearchClient = LLMResearchClient()

