"""Unit tests for Python interpreter."""
import asyncio

import homeassistant.components.pyscript.eval as eval
import homeassistant.components.pyscript.handler as handler
import homeassistant.components.pyscript.state as state

evalTests = [
    ["1", 1],
    ["1+1", 2],
    ["1+2*3-2", 5],
    ["1-1", 0],
    ["z = 1+2+3; a = z + 1; a + 3", 10],
    ["z = 1+2+3; a = z + 1; a - 3", 4],
    ["x = 1; x < 2", 1],
    ["x = 1; 0 < x < 2", 1],
    ["x = 1; 0 < x < 2 < -x", 0],
    ["1 and 2", 2],
    ["1 and 0", 0],
    ["0 or 1", 1],
    ["0 or 0", 0],
    ["z = 'foo'; z + 'bar'", "foobar"],
    ["xyz.y = 5; xyz.y = 2 + int(xyz.y); int(xyz.y)", 7],
    ["z = 'abcd'; z.find('c')", 2],
    ["'abcd'.upper().lower().upper()", "ABCD"],
    ["len('abcd')", 4],
    ["6 if 1-1 else 2", 2],
    ["x = 1; x += 3; x", 4],
    ["if 1: x = 10\nelse: x = 20\nx", 10],
    ["if 0: x = 10\nelse: x = 20\nx", 20],
    ["i = 0\nwhile i < 5: i += 1\ni", 5],
    ["i = 0\nwhile i < 5: i += 2\ni", 6],
    ["i = 0\nwhile i < 5:\n    i += 1\n    if i == 3: break\n2 * i", 6],
    [
        "i = 0; k = 10\nwhile i < 5:\n    i += 1\n    if i <= 2: continue\n    k += 1\nk + i",
        18,
    ],
    ["i = 1; break; i = 1/0", None],
    ["s = 0;\nfor i in range(5):\n    s += i\ns", 10],
    [
        "z = {'foo': 'bar', 'foo2': 12}; z['foo'] = 'bar2'; z",
        {"foo": "bar2", "foo2": 12},
    ],
    ["z = {'foo': 'bar', 'foo2': 12}; z['foo'] = 'bar2'; z.keys()", {"foo", "foo2"}],
    ["z = {'foo', 'bar', 12}; z", {"foo", "bar", 12}],
    [
        "x = dict(key1 = 'value1', key2 = 'value2'); x",
        {"key1": "value1", "key2": "value2"},
    ],
    [
        "x = dict(key1 = 'value1', key2 = 'value2', key3 = 'value3'); del x['key1']; x",
        {"key2": "value2", "key3": "value3"},
    ],
    [
        "x = dict(key1 = 'value1', key2 = 'value2', key3 = 'value3'); del x[['key1', 'key2']]; x",
        {"key3": "value3"},
    ],
    ["z = {'foo', 'bar', 12}; z.remove(12); z.add(20); z", {"foo", "bar", 20}],
    ["z = [0, 1, 2, 3, 4, 5, 6]; z[1:5:2] = [4, 5]; z", [0, 4, 2, 5, 4, 5, 6]],
    ["import random as rand, math as m\n[rand.uniform(10,10), m.sqrt(1024)]", [10, 32]],
    ["from math import sqrt as sqroot\nsqroot(1024)", 32],
    [
        """
bar = 100
def foo(bar=6):
    bar += 2
    return bar
    bar += 5
    return 1000
[foo(), foo(5), bar]
""",
        [8, 7, 100],
    ],
    [
        """
def foo(cnt=4):
    sum = 0
    for i in range(cnt):
        sum += i
        if i == 6:
            return 1000 + sum
        if i == 7:
            break
    return sum
[foo(3), foo(6), foo(10), foo(20), foo()]
""",
        [
            sum(range(3)),
            sum(range(6)),
            1000 + sum(range(7)),
            1000 + sum(range(7)),
            sum(range(4)),
        ],
    ],
    [
        """
def foo(cnt):
    sum = 0
    for i in range(cnt):
        sum += i
        if i != 6:
            pass
        else:
            return 1000 + sum
        if i == 7:
            break
    return sum
[foo(3), foo(6), foo(10), foo(20)]
""",
        [sum(range(3)), sum(range(6)), 1000 + sum(range(7)), 1000 + sum(range(7))],
    ],
    [
        """
def foo(cnt):
    sum = 0
    i = 0
    while i < cnt:
        sum += i
        if i != 6:
            pass
        else:
            return 1000 + sum
        if i == 7:
            break
        i += 1
    return sum
[foo(3), foo(6), foo(10), foo(20)]
""",
        [sum(range(3)), sum(range(6)), 1000 + sum(range(7)), 1000 + sum(range(7))],
    ],
    [
        """
def foo(x=30, *args, y = 123, **kwargs):
    return [x, y, args, kwargs]
[foo(a = 10, b = 3), foo(40, 7, 8, 9, a = 10, y = 3), foo(x=42)]
""",
        [
            [30, 123, (), {"a": 10, "b": 3}],
            [40, 3, (7, 8, 9), {"a": 10}],
            [42, 123, (), {}],
        ],
    ],
]


async def runOneTest(t):
    """Run one interpreter test."""
    str, expect = t
    a = eval.AstEval("test")
    a.parse(str)
    if a.getException() is not None:
        print(f"Parsing {str} failed: {a.getException()}")
    # print(a.dump())
    result = await a.eval()
    assert result == expect


def test_eval(hass):
    """Test interpreter."""
    state.hassSet(hass)
    handler.hassSet(hass)
    for t in evalTests:
        asyncio.run(runOneTest(t))
