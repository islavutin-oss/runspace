"""A turn's rendered blocks must belong to that turn.

A tool registers its real ```chart block and receives a {"$mcpui": N}
placeholder to position; restore_mcp_ui_blocks() splices the canonical block
back before the reply ships, so a model cannot invent chart data.

The blocks were held in a module-level list shared by the whole process. Two
concurrent turns therefore shared one list: the second turn's begin_turn()
cleared the first's blocks, and whichever turn drained first took all of them.
The loser had nothing to splice, and its reply reached the reader as a literal

    ```chart
    {"$mcpui": 1}
    ```

Reproduced against a live workspace by charting from two sessions at once: one
reply rendered nine points, the other shipped the placeholder.
"""

import contextvars

from runspace.workspace.backend._mcp_ui import (
    begin_turn,
    register_block,
    restore_mcp_ui_blocks,
)

CHART = '```chart\n{"type": "line", "data": [{"vus": %d}]}\n```'


def test_a_placeholder_is_returned_for_the_model_to_position():
    begin_turn()
    marker = register_block(CHART % 1)
    assert "$mcpui" in marker
    assert marker.startswith("```chart")


def test_a_turn_splices_its_own_block():
    begin_turn()
    marker = register_block(CHART % 1)
    out = restore_mcp_ui_blocks(f"here is the curve\n\n{marker}\n\nanything else?")
    assert "$mcpui" not in out
    assert '"vus": 1' in out


def test_two_turns_keep_their_own_blocks():
    results = {}

    def turn(name: str, vus: int) -> None:
        begin_turn()
        marker = register_block(CHART % vus)
        results[name] = restore_mcp_ui_blocks(f"reply {name}\n{marker}")

    for name, vus in (("a", 11), ("b", 22)):
        contextvars.copy_context().run(turn, name, vus)

    assert '"vus": 11' in results["a"] and '"vus": 22' not in results["a"]
    assert '"vus": 22' in results["b"] and '"vus": 11' not in results["b"]
    assert all("$mcpui" not in v for v in results.values())


def test_interleaved_turns_do_not_clear_each_other():
    """Both register before either restores — the actual interleaving."""
    held = {}

    def begin(name, vus):
        begin_turn()
        held[name] = register_block(CHART % vus)

    def finish(name):
        held[name + "_out"] = restore_mcp_ui_blocks(held[name])

    a, b = contextvars.copy_context(), contextvars.copy_context()
    a.run(begin, "a", 11)
    b.run(begin, "b", 22)  # used to clear a's list
    a.run(finish, "a")
    b.run(finish, "b")

    assert '"vus": 11' in held["a_out"] and "$mcpui" not in held["a_out"]
    assert '"vus": 22' in held["b_out"] and "$mcpui" not in held["b_out"]


def test_several_blocks_in_one_turn_keep_their_order():
    begin_turn()
    first, second = register_block(CHART % 1), register_block(CHART % 2)
    out = restore_mcp_ui_blocks(f"{first}\n\n{second}")
    assert out.index('"vus": 1') < out.index('"vus": 2')
    assert "$mcpui" not in out


def test_a_turn_that_registered_nothing_is_left_alone():
    begin_turn()
    text = "no chart here, just words"
    assert restore_mcp_ui_blocks(text) == text
