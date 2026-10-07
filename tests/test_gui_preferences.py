import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from gui_preferences import enable_no_reasoning


def test_none_becomes_available_without_changing_history_or_saved_selections():
    original = {
        "thread-project-assignments": {"thread": "project"},
        "electron-persisted-atom-state": {
            "enabled-reasoning-efforts": ["low", "xhigh"],
            "reasoning-selection": "xhigh",
            "onboarding": {"completed": True},
        },
        "project-names": ["Research α"],
    }
    expected = json.loads(json.dumps(original))
    expected["electron-persisted-atom-state"]["enabled-reasoning-efforts"].insert(0, "none")
    merged = enable_no_reasoning(json.dumps(original))
    assert json.loads(merged) == expected
    assert enable_no_reasoning(merged) == merged


def test_fresh_profile_exposes_none_and_existing_vendor_defaults():
    efforts = json.loads(enable_no_reasoning("{}"))["electron-persisted-atom-state"]["enabled-reasoning-efforts"]
    assert efforts == ["none", "low", "medium", "high", "xhigh", "ultra", "persistent"]


@pytest.mark.parametrize("state", ["[]", '{"electron-persisted-atom-state": []}',
    '{"electron-persisted-atom-state": {"enabled-reasoning-efforts": "low"}}'])
def test_unrecognized_state_fails_without_overwriting_it(state):
    with pytest.raises(ValueError):
        enable_no_reasoning(state)
