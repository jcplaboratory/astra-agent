from uuid import uuid4

from astra_ara.settings import ARASettings


def test_ara_settings_use_documented_environment_names(
    monkeypatch: object, tmp_path: object
) -> None:
    tenant_id = uuid4()
    ara_id = uuid4()
    monkeypatch.setenv("ASTRA_ARA_TENANT_ID", str(tenant_id))
    monkeypatch.setenv("ASTRA_ARA_ID", str(ara_id))
    monkeypatch.setenv("ASTRA_ARA_AGENT_URL", "https://localhost:8443")
    monkeypatch.setenv("ASTRA_ARA_REPOSITORY_ROOT", str(tmp_path))
    settings = ARASettings()
    assert settings.tenant_id == tenant_id
    assert settings.ara_id == ara_id
    assert settings.agent_url == "https://localhost:8443"
