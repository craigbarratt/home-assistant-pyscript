"""Test the pyscript component."""
from ast import literal_eval
import asyncio
from datetime import datetime as dt
import time

import homeassistant.components.pyscript.trigger as trigger
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.setup import async_setup_component

from tests.async_mock import mock_open, patch


async def setup_script(hass, notifyQ, now, source):
    """Initialize and load the given pyscript."""
    scripts = [
        "/some/config/dir/pyscripts/hello.py",
    ]
    with patch(
        "homeassistant.components.pyscript.os.path.isdir", return_value=True
    ), patch(
        "homeassistant.components.pyscript.glob.iglob", return_value=scripts
    ), patch(
        "homeassistant.components.pyscript.open",
        mock_open(read_data=source),
        create=True,
    ), patch(
        "homeassistant.components.pyscript.trigger.dt_now", return_value=now
    ):
        assert await async_setup_component(hass, "pyscript", {})

    #
    # I'm not sure how to run the mock all the time, so just force the dt_now()
    # trigger function to return the fixed time, now.
    #
    trigger.__dict__["dt_now"] = lambda: now

    if notifyQ:

        async def state_changed(event):
            varName = event.data["entity_id"]
            if varName != "pyscript.done":
                return
            value = event.data["new_state"].state
            await notifyQ.put(value)

        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)


async def wait_until_done(notifyQ):
    """Wait for the done handshake."""
    return await asyncio.wait_for(notifyQ.get(), timeout=4)


async def test_state_trigger(hass, caplog):
    """Test state trigger."""
    notifyQ = asyncio.Queue(0)
    await setup_script(
        hass,
        notifyQ,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """

from math import sqrt

seqNum = 0

@time_trigger
def funcStartupSync():
    global seqNum

    seqNum += 1
    log.info(f"funcStartupSync setting pyscript.done = {seqNum}")
    pyscript.done = seqNum

@state_trigger("pyscript.f1var1 == '1'")
def func1(var_name=None, value=None):
    global seqNum

    seqNum += 1
    log.info(f"func1 var = {var_name}, value = {value}")
    pyscript.done = [seqNum, var_name, int(value), sqrt(1024)]

@state_trigger("pyscript.f1var1 == '1' or pyscript.f2var2 == '2'")
@state_active("pyscript.f2var3 == '3' and pyscript.f2var4 == '4'")
def func2(var_name=None, value=None):
    global seqNum

    seqNum += 1
    log.info(f"func2 var = {var_name}, value = {value}")
    pyscript.done = [seqNum, var_name, int(value), sqrt(4096)]

@event_trigger("test_event3", "arg1 == 20 and arg2 == 30")
def func3(trigger_type=None, event_type=None, **kwargs):
    global seqNum

    seqNum += 1
    log.info(f"func3 trigger_type = {trigger_type}, event_type = {event_type}, event_data = {kwargs}")
    pyscript.done = [seqNum, trigger_type, event_type, kwargs]

@event_trigger("test_event4", "arg1 == 20 and arg2 == 30")
def func4(trigger_type=None, event_type=None, **kwargs):
    global seqNum

    seqNum += 1
    res = task.wait_until(event_trigger=["test_event4b", "arg1 == 25 and arg2 == 35"], timeout=10)
    log.info(f"func4 trigger_type = {res}, event_type = {event_type}, event_data = {kwargs}")
    pyscript.done = [seqNum, res, event_type, kwargs]

    seqNum += 1
    res = task.wait_until(state_trigger="pyscript.f4var2 == '2'", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seqNum, res]

    pyscript.setVar1 = 1
    pyscript.setVar2 = "var2"
    state.set("pyscript.setVar3", {"foo": "bar"})
    state.set("pyscript.setVar1", 1 + int(state.get("pyscript.setVar1")))

    seqNum += 1
    res = task.wait_until(state_trigger="pyscript.f4var2 == '10'", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seqNum, res, pyscript.setVar1, pyscript.setVar2, state.get("pyscript.setVar3")]

    seqNum += 1
    #
    # now() returns 1usec before 2020/7/1 12:00:00, so trigger right
    # at noon
    #
    res = task.wait_until(time_trigger="once(2020/07/01 12:00:00)", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seqNum, res]

    seqNum += 1
    #
    # this should pick up the trigger interval at noon
    #
    res = task.wait_until(time_trigger="period(2020/07/01 11:00, 1 hour)", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seqNum, res]

    seqNum += 1
    #
    # cron triggers at 10am, 11am, noon, 1pm, 2pm, 3pm, so this
    # should trigger at noon.
    #
    res = task.wait_until(time_trigger="cron(0 10-15 * * *)", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seqNum, res]

    seqNum += 1
    #
    # also add some month and day ranges; should still trigger at noon
    # on 7/1.
    #
    res = task.wait_until(time_trigger="cron(0 10-15 1-5 6,7 *)", timeout=10)
    log.info(f"func4 trigger_type = {res}")
    pyscript.done = [seqNum, res]


""",
    )
    seqNum = 0

    seqNum += 1
    # fire event to startup triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert literal_eval(await wait_until_done(notifyQ)) == seqNum

    seqNum += 1
    # initialize the trigger and active variables
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f2var2", 0)
    hass.states.async_set("pyscript.f2var3", 0)
    hass.states.async_set("pyscript.f2var4", 0)

    # try some values that shouldn't work, then one that does
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f1var1", "string")
    hass.states.async_set("pyscript.f1var1", -1)
    hass.states.async_set("pyscript.f1var1", 1)
    assert literal_eval(await wait_until_done(notifyQ)) == [
        seqNum,
        "pyscript.f1var1",
        1,
        32,
    ]
    assert "func1 var = pyscript.f1var1, value = 1" in caplog.text

    seqNum += 1
    hass.states.async_set("pyscript.f2var3", 3)
    hass.states.async_set("pyscript.f2var4", 0)
    hass.states.async_set("pyscript.f2var2", 0)
    hass.states.async_set("pyscript.f1var1", 0)
    hass.states.async_set("pyscript.f1var1", 1)
    assert literal_eval(await wait_until_done(notifyQ)) == [
        seqNum,
        "pyscript.f1var1",
        1,
        32,
    ]

    seqNum += 1
    hass.states.async_set("pyscript.f2var4", 4)
    hass.states.async_set("pyscript.f2var2", 2)
    assert literal_eval(await wait_until_done(notifyQ)) == [
        seqNum,
        "pyscript.f2var2",
        2,
        64,
    ]
    assert "func2 var = pyscript.f2var2, value = 2" in caplog.text

    seqNum += 1
    hass.bus.async_fire("test_event3", {"arg1": 12, "arg2": 34})
    hass.bus.async_fire("test_event3", {"arg1": 20, "arg2": 29})
    hass.bus.async_fire("test_event3", {"arg1": 12, "arg2": 30})
    hass.bus.async_fire("test_event3", {"arg1": 20, "arg2": 30})
    assert literal_eval(await wait_until_done(notifyQ)) == [
        seqNum,
        "event",
        "test_event3",
        {"arg1": 20, "arg2": 30},
    ]

    seqNum += 1
    hass.states.async_set("pyscript.f4var2", 2)
    hass.bus.async_fire("test_event4", {"arg1": 20, "arg2": 30})
    t = time.monotonic()
    while notifyQ.empty() and t < time.monotonic() + 4:
        hass.bus.async_fire("test_event4b", {"arg1": 15, "arg2": 25})
        hass.bus.async_fire("test_event4b", {"arg1": 20, "arg2": 25})
        hass.bus.async_fire("test_event4b", {"arg1": 25, "arg2": 35})
        await asyncio.sleep(1e-3)
    trig = {
        "trigger_type": "event",
        "event_type": "test_event4b",
        "arg1": 25,
        "arg2": 35,
    }
    assert literal_eval(await wait_until_done(notifyQ)) == [
        seqNum,
        trig,
        "test_event4",
        {"arg1": 20, "arg2": 30},
    ]

    seqNum += 1
    # the state_trigger wait_until should succeed immediately, since the expr is true
    assert literal_eval(await wait_until_done(notifyQ)) == [
        seqNum,
        {"trigger_type": "state"},
    ]

    seqNum += 1
    # now try a few other values, then the correct one
    hass.states.async_set("pyscript.f4var2", 4)
    hass.states.async_set("pyscript.f4var2", 2)
    hass.states.async_set("pyscript.f4var2", 10)
    trig = {
        "trigger_type": "state",
        "var_name": "pyscript.f4var2",
        "value": "10",
        "old_value": "2",
    }
    r = literal_eval(await wait_until_done(notifyQ))
    assert r[0] == seqNum
    assert r[1] == trig

    assert hass.states.get("pyscript.setVar1").state == "2"
    assert hass.states.get("pyscript.setVar2").state == "var2"
    assert literal_eval(hass.states.get("pyscript.setVar3").state) == {"foo": "bar"}

    for i in range(4):
        # the four time triggers should happen almost immediately
        seqNum += 1
        assert literal_eval(await wait_until_done(notifyQ)) == [
            seqNum,
            {"trigger_type": "time"},
        ]
