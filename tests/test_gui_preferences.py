import json
import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gui_preferences import enable_no_reasoning


def test_none_is_saved_in_current_config_without_changing_other_preferences():
    config = '''# preserve this comment
model_reasoning_effort = "xhigh"
[desktop]
enabled-reasoning-efforts = ["low", "xhigh"]
runCodexInWindowsSubsystemForLinux = true
[projects."/srv/research"]
trust_level = "trusted"
'''
    expected = tomllib.loads(config)
    expected["desktop"]["enabled-reasoning-efforts"].insert(0, "none")
    merged = enable_no_reasoning(config)
    assert tomllib.loads(merged) == expected
    assert "# preserve this comment" in merged
    assert enable_no_reasoning(merged) == merged


def test_legacy_choices_are_imported_without_writing_legacy_state():
    legacy = json.dumps({"thread-project-assignments": {"thread": "project"},
        "electron-persisted-atom-state": {"enabled-reasoning-efforts": ["low", "xhigh"], "onboarding": True}})
    merged = tomllib.loads(enable_no_reasoning('model = "local"', legacy))
    assert merged["desktop"]["enabled-reasoning-efforts"] == ["none", "low", "xhigh"]
    assert merged["model"] == "local"


def test_current_config_takes_precedence_over_stale_legacy_choices():
    config = '[desktop]\nenabled-reasoning-efforts = ["medium"]\n'
    legacy = '{"electron-persisted-atom-state":{"enabled-reasoning-efforts":["low","xhigh"]}}'
    assert tomllib.loads(enable_no_reasoning(config, legacy))["desktop"]["enabled-reasoning-efforts"] == ["none", "medium"]


@pytest.mark.parametrize("config", ["", "[desktop]", "model_reasoning_effort = 'low'"])
def test_missing_preference_exposes_none_and_vendor_defaults(config):
    result = tomllib.loads(enable_no_reasoning(config))
    assert result["desktop"]["enabled-reasoning-efforts"] == ["none", "low", "medium", "high", "xhigh", "ultra", "persistent"]


def test_multiline_array_and_quoted_desktop_table():
    config = '''["desktop"] # keep header
"enabled-reasoning-efforts" = [
  "low", # choice
  "xhigh",
]
# keep other preference
followUpQueueMode = "steer"
'''
    merged = enable_no_reasoning(config)
    assert tomllib.loads(merged)["desktop"] == {"enabled-reasoning-efforts": ["none", "low", "xhigh"], "followUpQueueMode": "steer"}
    assert "# keep header" in merged and "# keep other preference" in merged


@pytest.mark.parametrize("config,legacy", [
    ("", "[]"),
    ("", '{"electron-persisted-atom-state": []}'),
    ("", '{"electron-persisted-atom-state":{"enabled-reasoning-efforts":"low"}}'),
    ('[desktop]\nenabled-reasoning-efforts = "low"', "{}"),
])
def test_unrecognized_state_fails_without_overwriting_it(config, legacy):
    with pytest.raises(ValueError):
        enable_no_reasoning(config, legacy)
