import pytest

from codingmix.classifier import Classifier
from codingmix.config import load_config


@pytest.fixture
def clf():
    return Classifier(load_config().rules, window_seconds=180, switch_after_seconds=180)


def pre(tool, session="s1", **tool_input):
    return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
            "session_id": session, "permission_mode": "default"}


@pytest.mark.parametrize("payload, mode", [
    ({"hook_event_name": "PostToolUseFailure", "tool_name": "Bash"}, "debugging"),
    (pre("Skill", skill="superpowers:systematic-debugging"), "debugging"),
    ({**pre("Read", file_path="a.py"), "permission_mode": "plan"}, "planning"),
    (pre("EnterPlanMode"), "planning"),
    (pre("Skill", skill="superpowers:writing-plans"), "planning"),
    (pre("Skill", skill="superpowers:brainstorming"), "brainstorming"),
    (pre("AskUserQuestion"), "brainstorming"),
    (pre("Bash", command="git push origin main"), "release"),
    (pre("PowerShell", command="gh pr create --fill"), "release"),
    (pre("Bash", command="uv run pytest -q"), "testing"),
    (pre("Bash", command="pnpm run test"), "testing"),
    (pre("Skill", skill="code-review"), "reviewing"),
    (pre("Bash", command="git diff HEAD~1"), "reviewing"),
    ({"hook_event_name": "SubagentStart", "agent_type": "Explore"}, "exploring"),
    ({"hook_event_name": "SubagentStart", "agent_type": "general-purpose"}, "orchestrating"),
    (pre("Workflow"), "orchestrating"),
    (pre("mcp__claude-in-chrome__navigate"), "ui"),
    (pre("Edit", file_path="src/App.tsx"), "ui"),
    (pre("Write", file_path="docs/NOTES.md"), "writing"),
    (pre("Edit", file_path="src/app.py"), "coding"),
    (pre("Agent", subagent_type="Explore"), "exploring"),
    (pre("Agent", subagent_type="general-purpose"), "orchestrating"),
    (pre("Grep", pattern="x"), "exploring"),
    (pre("WebSearch", query="x"), "exploring"),
    (pre("Skill", skill="superpowers:requesting-code-review"), "reviewing"),
    ({**pre("Glob", pattern="*.py"), "agent_id": "a1", "agent_type": "Explore"}, "exploring"),
])
def test_classify_rules(clf, payload, mode):
    assert clf.classify(payload)[0] == mode


@pytest.mark.parametrize("payload", [
    {"hook_event_name": "Notification", "notification_type": "idle_prompt"},
    {"hook_event_name": "SessionEnd", "reason": "other"},
    {"hook_event_name": "Stop"},
    {"hook_event_name": "UserPromptSubmit", "prompt": "ciao, come va?"},
    # Claude Code also fires UserPromptSubmit for messages it injects itself.
    {"hook_event_name": "UserPromptSubmit",
     "prompt": "<task-notification>\n<status>failed</status> error in the plan review"},
    {"hook_event_name": "UserPromptSubmit",
     "prompt": '<agent-message from="a1">\nbug found, see the stack trace'},
    {"hook_event_name": "UserPromptSubmit", "permission_mode": "plan",
     "prompt": "  <task-notification>\n<status>completed</status>"},
    {},
])
def test_signals_without_vote(clf, payload):
    assert clf.classify(payload) is None


def test_edit_with_string_tool_input_still_counts_as_coding(clf):
    payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit", "tool_input": "x"}
    assert clf.classify(payload) == ("coding", 1.0)


def test_prompt_keywords_are_a_weak_vote(clf):
    payload = {"hook_event_name": "UserPromptSubmit", "prompt": "C'è un ERRORE nel login"}
    assert clf.classify(payload) == ("debugging", 0.5)


def test_first_leader_is_adopted_immediately(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    assert clf.stable_mode(0) == "coding"


def test_switch_needs_three_minutes_of_leadership(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    assert clf.stable_mode(0) == "coding"
    for second in range(200, 380, 10):
        clf.observe({"hook_event_name": "PostToolUseFailure"}, now=second)
        clf.stable_mode(second)
    # coding vote expired at 180; debugging leads since 200; switch at 380.
    assert clf.stable_mode(370) == "coding"
    assert clf.stable_mode(380) == "debugging"


def test_interrupted_leadership_resets_the_timer(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    clf.stable_mode(0)
    clf.observe({"hook_event_name": "PostToolUseFailure"}, now=190)
    assert clf.stable_mode(190) == "coding"
    for second in range(200, 300, 5):
        clf.observe(pre("Edit", file_path="a.py"), now=second)
        clf.observe(pre("Edit", file_path="b.py"), now=second)
    assert clf.stable_mode(300) == "coding"
    assert clf.snapshot(300)["candidate"] is None


def test_empty_window_keeps_the_stable_mode(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    clf.stable_mode(0)
    assert clf.stable_mode(10_000) == "coding"
    assert clf.leader(10_000) is None


def test_votes_from_all_sessions_are_summed(clf):
    clf.observe(pre("Edit", session="a", file_path="a.py"), now=0)
    clf.observe(pre("Bash", session="b", command="pytest"), now=1)
    clf.observe(pre("Bash", session="c", command="pytest"), now=2)
    assert clf.leader(3) == "testing"


def test_manual_mode_wins_until_it_expires(clf):
    clf.observe(pre("Edit", file_path="a.py"), now=0)
    clf.set_manual("release", until=100)
    assert clf.stable_mode(50) == "release"
    assert clf.stable_mode(101) == "coding"
    clf.set_manual("release", until=1000)
    clf.clear_manual()
    assert clf.stable_mode(102) == "coding"
