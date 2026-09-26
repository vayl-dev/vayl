"""
Configuration errors must name the setting and fail at startup — never be silently reinterpreted.
"""
import pytest

from vayl.config import env_float, env_int
from vayl.memory.llm_client import _provider
from vayl.security import kms


def test_numeric_settings_parse_and_default(monkeypatch):
    monkeypatch.setenv("VAYL_T_INT", "42")
    monkeypatch.setenv("VAYL_T_FLOAT", "0.25")
    assert env_int("VAYL_T_INT", 1) == 42
    assert env_float("VAYL_T_FLOAT", 1.0) == 0.25
    monkeypatch.setenv("VAYL_T_INT", "  ")                  # blank means "unset"
    assert env_int("VAYL_T_INT", 7) == 7
    assert env_int("VAYL_T_MISSING", 7) == 7


def test_a_malformed_number_names_the_variable(monkeypatch):
    monkeypatch.setenv("VAYL_PORT", "eighty")
    with pytest.raises(ValueError, match="VAYL_PORT must be an integer, got 'eighty'"):
        env_int("VAYL_PORT", 8080)


@pytest.mark.parametrize("value", ["openia", "ollama"])
def test_an_unknown_llm_provider_is_rejected_not_routed_to_anthropic(monkeypatch, value):
    monkeypatch.setenv("LLM_PROVIDER", value)
    with pytest.raises(ValueError, match="LLM_PROVIDER must be one of"):
        _provider()


def test_known_llm_providers_are_accepted_case_insensitively(monkeypatch):
    for value, expected in [("OpenAI", "openai"), ("anthropic", "anthropic"), (" groq ", "groq")]:
        monkeypatch.setenv("LLM_PROVIDER", value)
        assert _provider() == expected


def test_a_kms_typo_does_not_silently_fall_back_to_a_key_file(monkeypatch, tmp_path):
    monkeypatch.setenv("VAYL_KMS", "vualt")
    with pytest.raises(ValueError, match="VAYL_KMS must be 'file' or 'vault'"):
        kms.data_key(str(tmp_path / "k"))
    assert not (tmp_path / "k").exists()                   # no key file was minted


def test_vault_mode_requires_a_token(monkeypatch, tmp_path):
    monkeypatch.setenv("VAYL_KMS", "vault")
    monkeypatch.delenv("VAULT_TOKEN", raising=False)
    with pytest.raises(ValueError, match="requires VAULT_TOKEN"):
        kms.data_key(str(tmp_path / "k"))
