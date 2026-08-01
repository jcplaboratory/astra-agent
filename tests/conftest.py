import pytest


@pytest.fixture(autouse=True)
def isolate_local_model_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ASTRA_MODEL_BACKEND", "development")
    monkeypatch.delenv("ASTRA_OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("ASTRA_TUI_DEV_LOGIN", "false")
