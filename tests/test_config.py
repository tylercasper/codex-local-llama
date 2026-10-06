from codex_local_provider.config import Settings


def test_unreadable_key_content_selects_no_key_search(monkeypatch, tmp_path):
    key = tmp_path / 'tavily.key'
    key.write_bytes(b'\xff\xfe')
    monkeypatch.delenv('TAVILY_API_KEY', raising=False)
    monkeypatch.setenv('TAVILY_API_KEY_FILE', str(key))
    assert Settings.from_environment().tavily_api_key is None
