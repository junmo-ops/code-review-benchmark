import re

import networkx.readwrite.tests.test_gml as test_gml_mod


def test_gml_exceptions_test_uses_real_unicode_escape_not_legacy_u_prefix_or_backslash_escape():
    """
    The reviewed change updated the *test data string* inside
    networkx/readwrite/tests/test_gml.py to use a real unicode escape '\\u4200'
    inside quotes, and removed the legacy python2-style u'...' prefix and the
    literal backslash escape '\\\\4200'.

    This test asserts that the module's source for TestGraph.test_exceptions
    contains the updated GML snippet and does not contain the old one.
    """
    import inspect

    src = inspect.getsource(test_gml_mod.TestGraph.test_exceptions)

    # Must include the new intended unicode escape form (as text in the source).
    assert r"source '\u4200' target '\u4200'" in src, (
        "Expected TestGraph.test_exceptions to use a real unicode escape "
        "in the GML snippet: source '\\u4200' target '\\u4200'. "
        "This is the reviewed change."
    )
    assert r"node [ id '\u4200' label b ]" in src, (
        "Expected TestGraph.test_exceptions to use the unicode escape for node id "
        "as well: id '\\u4200'."
    )

    # Must NOT contain the old python2-style u'...' tokens nor the older '\\\\4200' form.
    assert "u'u\\4200'" not in src and "u'u\\\\4200'" not in src, (
        "Did not expect legacy python2-style u'...' prefixes with \\4200 or \\\\4200 "
        "to remain in TestGraph.test_exceptions."
    )

    # Stronger guard: ensure no occurrence of the specific old pattern for source/target.
    assert re.search(r"source\s+u'u\\\\?4200'\s+target\s+u'u\\\\?4200'", src) is None, (
        "Found old GML snippet using source/target u'u\\\\4200' (or u'u\\4200') "
        "which should have been replaced by '\\u4200'."
    )