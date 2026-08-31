"""Per-agent opening questions must survive workspace.yml → API.

The regression this guards: `suggestions` was added to the canonical checkout
and verified in a browser while the demo still resolved runspace from disk.
When the demo moved to the published package the field was simply not there,
the field never reached the frontend, and every agent fell back to the generic
"What can you help me with?" openers — with no error anywhere to notice.
"""

from runspace.workspace.backend.app_registry import AgentApp


def test_agentapp_defaults_to_no_suggestions():
    spec = AgentApp(id="a", name="A", role="r")
    assert spec.suggestions == []
    assert spec.to_dict()["suggestions"] == []


def test_suggestions_reach_the_api_payload():
    # to_dict is what /api/workspace/apps and /api/workspace/config both serve.
    # Dropping the key here is invisible until a UI silently shows defaults.
    qs = ["How much of this catalogue has anyone measured?", "What moved today?"]
    spec = AgentApp(id="models", name="Ada", role="r", suggestions=qs)
    assert spec.to_dict()["suggestions"] == qs


def test_each_spec_gets_its_own_list():
    # A shared mutable default would let one agent's questions appear on another.
    a, b = AgentApp(id="a", name="A", role="r"), AgentApp(id="b", name="B", role="r")
    a.suggestions.append("only mine")
    assert b.suggestions == []
