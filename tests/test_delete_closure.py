"""Delete-closure semantics.

Plain asserts and a __main__ runner so this works today with no test
dependency, and drops into pytest unchanged once one is added.

    python3 tests/test_delete_closure.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zeropoint_agent.query import (  # noqa: E402
    delete_closure, orphan_closure, subtree_closure,
)


class FakeEntry:
    def __init__(self, parents):
        self.parents = list(parents)


class FakeDag:
    """Duck-types the only surface the closure helpers touch: .nodes."""

    def __init__(self, spec):
        self.nodes = {nid: FakeEntry(parents) for nid, parents in spec.items()}


def _echo_graph():
    """Mirrors what installing `echo` actually produces."""
    return FakeDag({
        "settings": [],
        "settings/zp_arch": ["settings"],
        "modules": [],
        "system": [],
        "system/docker": ["system"],
        "modules/echo": ["modules"],
        "modules/echo/zp_module_id": ["modules/echo"],
        "modules/echo/greeting": ["modules/echo"],
        "modules/echo/terraform": [
            "settings/zp_arch", "modules/echo",
            "modules/echo/zp_module_id", "modules/echo/greeting",
        ],
        "modules/echo/main_ports": ["modules/echo/terraform"],
        "modules/echo/main_ports/http": ["modules/echo/main_ports"],
        # A second module, to prove isolation.
        "modules/other": ["modules"],
        "modules/other/terraform": ["settings/zp_arch", "modules/other"],
    })


def test_deleting_a_module_takes_its_whole_subtree():
    dag = _echo_graph()
    got = set(delete_closure(dag, {"modules/echo"}))
    assert got == {
        "modules/echo/zp_module_id",
        "modules/echo/greeting",
        "modules/echo/terraform",
        "modules/echo/main_ports",
        "modules/echo/main_ports/http",
    }, got


def test_deleting_a_module_leaves_other_modules_alone():
    dag = _echo_graph()
    got = set(delete_closure(dag, {"modules/echo"}))
    assert not any(nid.startswith("modules/other") for nid in got), got


def test_shared_dependency_does_not_cascade_into_modules():
    """The blast-radius guard.

    Every module's terraform lists settings/zp_arch as a parent. A naive
    descendant-cascade would take every module in the graph with it.
    """
    dag = _echo_graph()
    got = set(delete_closure(dag, {"settings/zp_arch"}))
    assert got == set(), got


def test_user_var_linked_to_a_module_survives_the_module():
    """A var the user made and linked to a module input is not swept up.

    This is the case the orphan rule looks like it should break. It
    doesn't, and the reason is worth pinning down: creation parents the
    node to its namespace (`NewNodePage.tsx` sends `parents: [parentId]`)
    and `link_var` *appends* the target, dropping only a previous Var
    parent. So the var keeps `settings` and merely loses an edge.

    Assert the realistic shape, not a hand-built one — a fixture without
    the namespace parent is a node the app cannot actually produce, and
    testing it only proves something about the fixture.
    """
    dag = _echo_graph()
    dag.nodes["settings/my_var"] = FakeEntry(
        ["settings", "modules/echo/greeting"])
    got = set(delete_closure(dag, {"modules/echo"}))
    assert "settings/my_var" not in got, got


def test_orphan_rule_catches_dependency_only_children():
    """A node held up solely by the deleted node comes along."""
    dag = FakeDag({
        "a": [],
        "b": ["a"],          # only parent is a -> orphaned
        "c": ["a", "keep"],  # has another parent -> survives
        "keep": [],
    })
    got = set(orphan_closure(dag, {"a"}))
    assert got == {"b"}, got


def test_orphan_rule_expands_to_fixpoint():
    """Orphaning a node can orphan its own children, transitively."""
    dag = FakeDag({"a": [], "b": ["a"], "c": ["b"], "d": ["c"]})
    got = set(orphan_closure(dag, {"a"}))
    assert got == {"b", "c", "d"}, got


def test_roots_are_not_orphans():
    """Parentless nodes are roots, not orphans — never swept up."""
    dag = FakeDag({"modules": [], "settings": [], "system": [], "x": ["modules"]})
    got = set(orphan_closure(dag, {"x"}))
    assert got == set(), got


def test_subtree_is_prefix_based_not_substring():
    """`modules/echo` must not match `modules/echo-two`."""
    dag = FakeDag({
        "modules/echo": [],
        "modules/echo/var": ["modules/echo"],
        "modules/echo-two": [],
        "modules/echo-two/var": ["modules/echo-two"],
    })
    got = set(subtree_closure(dag, {"modules/echo"}))
    assert got == {"modules/echo/var"}, got


def test_closure_excludes_the_input_ids():
    """Returns only the *additional* ids, so callers can concatenate."""
    dag = _echo_graph()
    got = delete_closure(dag, {"modules/echo"})
    assert "modules/echo" not in got, got
    assert len(got) == len(set(got)), f"duplicates: {got}"


def test_parents_are_ordered_before_children():
    """Removal walks this reversed, so children must come last."""
    dag = _echo_graph()
    got = subtree_closure(dag, {"modules/echo"})
    assert got.index("modules/echo/main_ports") < \
        got.index("modules/echo/main_ports/http"), got


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {name}\n        {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
