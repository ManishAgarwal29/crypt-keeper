"""inspect.getargspec -> inspect.getfullargspec, only where the replacement provably means the same (found by the benchmark).

`getargspec` returned 4 fields (args, varargs, keywords, defaults). `getfullargspec` returns 7 and renames the third
`varkw`, so a plain rename breaks unpacking and length checks. These tests pin what is rewritten, what is left for a
person, and that the rewritten code computes the same values when it is actually run.
"""
import inspect

import pytest

from necromancer.rewriter import rewrite_deprecated


def rewrite(src: str, target: str = "3.12") -> tuple[str, list[str], list[str]]:
    return rewrite_deprecated(src, target, "m.py")


def run(src: str) -> dict:
    namespace: dict = {}
    exec(compile(src, "m.py", "exec"), namespace)  # noqa: S102  (source built in this file, run on the interpreter under test)
    return namespace


SAMPLE = "def sample(a, b=1, *rest, **extra):\n    pass\n\ndef plain(x):\n    pass\n"


# --- shapes that are rewritten, and then run ----------------------------------------------------------------------------


def test_four_names_unpacking_keeps_its_shape_and_gives_the_same_values():
    old = SAMPLE + "from inspect import getargspec\nargs, varargs, keywords, defaults = getargspec(sample)\n"
    new, applied, manual = rewrite(old)
    assert "getfullargspec(sample)[:4]" in new and "getargspec" not in new and manual == []
    ns = run(new)
    assert (ns["args"], ns["varargs"], ns["keywords"], ns["defaults"]) == (["a", "b"], "rest", "extra", (1,))


def test_a_local_that_is_only_read_as_fields_is_renamed_and_keywords_becomes_varkw():
    old = SAMPLE + (
        "from inspect import getargspec\n"
        "def describe(f):\n"
        "    spec = getargspec(f)\n"
        "    return spec.args, spec.varargs, spec.keywords, spec.defaults\n"
        "result = describe(sample)\n"
    )
    new, _, manual = rewrite(old)
    assert "spec.varkw" in new and "spec.keywords" not in new and "getargspec" not in new and manual == []
    assert run(new)["result"] == (["a", "b"], "rest", "extra", (1,))


def test_attribute_and_index_access_on_the_call_is_renamed():
    old = SAMPLE + "import inspect\na = inspect.getargspec(sample).args\nb = inspect.getargspec(sample).keywords\nc = inspect.getargspec(sample)[3]\nd = inspect.getargspec(sample)[2]\n"
    new, _, manual = rewrite(old)
    assert manual == [] and "getargspec" not in new
    ns = run(new)
    assert (ns["a"], ns["b"], ns["c"], ns["d"]) == (["a", "b"], "extra", (1,), "extra")


def test_any_unpacking_target_sees_the_same_values_after_the_slice():
    """Starred and list targets are rewritten too: the slice hands them the same four values `getargspec` did."""
    old = SAMPLE + (
        "from inspect import getargspec\n"
        "a, b, c, *d = getargspec(sample)\n"
        "*e, f = getargspec(sample)\n"
        "[g, h, i, j] = getargspec(sample)\n"
    )
    new, _, manual = rewrite(old)
    assert manual == [] and new.count("[:4]") == 3
    ns = run(new)
    assert (ns["a"], ns["b"], ns["c"], ns["d"]) == (["a", "b"], "rest", "extra", [(1,)])
    assert (ns["e"], ns["f"]) == ([["a", "b"], "rest", "extra"], (1,))
    assert (ns["g"], ns["h"], ns["i"], ns["j"]) == (["a", "b"], "rest", "extra", (1,))


def test_a_wrongly_sized_unpacking_fails_exactly_as_it_did_before():
    new, _, _ = rewrite("from inspect import getargspec\ndef g(f):\n    a, b, c = getargspec(f)\n")
    with pytest.raises(ValueError, match="too many values to unpack"):
        ns = run(new + "\n")
        ns["g"](lambda x, y=1, *z, **w: None)


def test_a_module_alias_is_followed():
    old = SAMPLE + "import inspect as ins\nx = ins.getargspec(plain).args\n"
    new, _, manual = rewrite(old)
    assert "ins.getfullargspec(plain).args" in new and manual == [] and run(new)["x"] == ["x"]


def test_an_import_alias_is_kept_and_only_the_imported_name_changes():
    old = SAMPLE + "from inspect import getargspec as gas\nx = gas(plain).args\n"
    new, _, manual = rewrite(old)
    assert "from inspect import getfullargspec as gas" in new and "x = gas(plain).args" in new and manual == []
    assert run(new)["x"] == ["x"]


def test_several_uses_in_one_file_are_all_rewritten_together():
    old = SAMPLE + "from inspect import getargspec\na = getargspec(plain).args\nb, c, d, e = getargspec(sample)\nf = getargspec(sample)[0]\n"
    new, _, manual = rewrite(old)
    assert "getargspec" not in new and manual == []
    ns = run(new)
    assert (ns["a"], ns["b"], ns["c"], ns["d"], ns["e"], ns["f"]) == (["x"], ["a", "b"], "rest", "extra", (1,), ["a", "b"])


def test_crlf_line_endings_and_comments_are_preserved():
    old = "from inspect import getargspec  # old\r\na, b, c, d = getargspec(f)  # unpack\r\n"
    new, _, _ = rewrite(old)
    assert new == "from inspect import getfullargspec  # old\r\na, b, c, d = getfullargspec(f)[:4]  # unpack\r\n"


# --- shapes that must stay with a person -------------------------------------------------------------------------------------


@pytest.mark.parametrize("old", [
    "import inspect\nspec = inspect.getargspec(f)\n",                                    # module-level name: other modules may read it
    "import inspect\ndef g(f):\n    return inspect.getargspec(f)\n",                      # returned: the caller may unpack it
    "import inspect\ndef g(f):\n    spec = inspect.getargspec(f)\n    return spec\n",     # the local escapes
    "import inspect\ndef g(f):\n    spec = inspect.getargspec(f)\n    return len(spec)\n",
    "import inspect\ndef g(f):\n    return len(inspect.getargspec(f))\n",
    "import inspect\ndef g(f):\n    return inspect.getargspec(f)[-1]\n",
    "import inspect\ndef g(f):\n    return inspect.getargspec(f)[4]\n",                    # one past the last of its 4 fields
    "import inspect\ndef g(f):\n    return inspect.getargspec(f)[6]\n",
    "import inspect\ndef g(f):\n    spec = inspect.getargspec(f)\n    return spec._asdict()\n",   # not one of the four fields
    "import inspect\ndef g(f):\n    spec = inspect.getargspec(f)\n    return spec.count(1)\n",
    "import inspect\ndef g(f):\n    class C:\n        spec = inspect.getargspec(f)\n        x = spec.args\n    return C\n",   # a class attribute is readable from outside
    "import inspect\ndef g(f):\n    return inspect.getargspec(f)[1:]\n",
    "import inspect\ndef g(f):\n    return inspect.getargspec(f)._asdict()\n",
    "import inspect\ndef g(f, spec):\n    spec = inspect.getargspec(f)\n    return spec.args\n",   # shadows a parameter
    "import inspect\ndef g(f):\n    spec = inspect.getargspec(f)\n    spec = 1\n    return spec.args\n",   # reassigned
    "import inspect\ndef g(f):\n    global spec\n    spec = inspect.getargspec(f)\n    return spec.args\n",
    "import inspect\nfn = inspect.getargspec\n",                                          # passed around, never called here
    "import inspect\nx = map(inspect.getargspec, [f])\n",
])
def test_a_use_that_is_not_provably_the_same_is_left_alone_and_reported(old):
    new, applied, manual = rewrite(old)
    assert new == old and applied == []
    assert manual and "getargspec" in manual[0]


def test_one_unprovable_use_of_an_imported_name_leaves_the_whole_import_alone():
    """Renaming the import would break the use that was not rewritten, so nothing is changed."""
    old = "from inspect import getargspec\na = getargspec(f).args\nb = getargspec(f)\n"
    new, applied, manual = rewrite(old)
    assert new == old and applied == [] and manual


def test_an_unprovable_attribute_use_does_not_stop_a_provable_one_elsewhere():
    old = "import inspect\na = inspect.getargspec(f).args\nb = inspect.getargspec(f)\n"
    new, _, manual = rewrite(old)
    assert "a = inspect.getfullargspec(f).args" in new and "b = inspect.getargspec(f)" in new and len(manual) == 1


def test_a_name_that_is_also_assigned_is_not_renamed():
    old = "from inspect import getargspec\na = getargspec(f).args\ngetargspec = lambda f: None\n"
    new, applied, _ = rewrite(old)
    assert new == old and applied == []


def test_a_use_inside_a_try_except_importerror_is_a_deliberate_fallback():
    old = "try:\n    from inspect import getfullargspec as spec\nexcept ImportError:\n    from inspect import getargspec as spec\n"
    new, applied, manual = rewrite(old)
    assert new == old and applied == [] and manual == []


def test_a_guarded_use_of_an_imported_name_blocks_renaming_the_import():
    old = "from inspect import getargspec\na = getargspec(f).args\ntry:\n    b = getargspec(f).args\nexcept ImportError:\n    pass\n"
    new, _, _ = rewrite(old)
    assert "from inspect import getargspec" in new, "a use that was not rewritten would lose its name"


def test_a_try_except_that_is_not_an_import_guard_does_not_block_the_rewrite():
    old = "from inspect import getargspec\na = getargspec(f).args\ntry:\n    b = getargspec(f).args\nexcept TypeError:\n    pass\n"
    new, _, manual = rewrite(old)
    assert "getargspec" not in new and manual == []


def test_nothing_is_rewritten_for_a_target_that_still_has_getargspec():
    old = "import inspect\na = inspect.getargspec(f).args\n"
    assert rewrite(old, "3.10") == (old, [], [])


def test_the_rewrite_is_idempotent():
    old = "from inspect import getargspec\ndef g(f):\n    s = getargspec(f)\n    a, b, c, d = getargspec(f)\n    return s.keywords\n"
    once, _, _ = rewrite(old)
    twice, applied, manual = rewrite(once)
    assert twice == once and applied == [] and manual == []


def test_the_rewritten_source_still_parses():
    import ast

    old = "from inspect import getargspec\na, b, c, d = getargspec(f)\ne = getargspec(f).keywords\n"
    ast.parse(rewrite(old)[0])


def test_getfullargspec_really_has_the_fields_this_rewrite_relies_on():
    """If a future Python changed these, the rewrite would be wrong; this fails first."""
    spec = inspect.getfullargspec(lambda a, b=1, *c, **d: None)
    assert spec._fields[:4] == ("args", "varargs", "varkw", "defaults") and tuple(spec[:4]) == (["a", "b"], "c", "d", (1,))
