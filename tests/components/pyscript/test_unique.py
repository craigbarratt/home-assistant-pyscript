"""Test the pyscript component."""
import asyncio
import logging
import time
from datetime import datetime as dt

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, EVENT_STATE_CHANGED
from homeassistant.components.pyscript import DOMAIN
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.setup import async_setup_component
import homeassistant.components.pyscript.trigger as trigger

from tests.async_mock import mock_open, patch
from tests.common import patch_yaml_files


async def setup_script(hass, notifyQ, now, source):
    """Setup with the given pyscript."""
    scripts = [
        "/some/config/dir/pyscripts/hello.py",
    ]
    with patch(
        "homeassistant.components.pyscript.os.path.isdir", return_value=True
    ), patch(
        "homeassistant.components.pyscript.glob.iglob", return_value=scripts
    ), patch(
        "homeassistant.components.pyscript.open", mock_open(read_data=source), create=True,
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
    return await asyncio.wait_for(notifyQ.get(), timeout=4)


async def test_task_unique(hass, caplog):
    """Test task.unique ."""
    notifyQ = asyncio.Queue(0)
    await setup_script(hass, notifyQ, dt(2020, 7, 1, 11, 59, 59, 999999), """ 

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
    task.unique("func1")
    pyscript.done = [seqNum, var_name]
    # this should terminate our task, so the 2nd done won't happen
    # if it did, we would get out of sequence in the assert
    task.unique("func1")
    pyscript.done = [seqNum, var_name]

@state_trigger("pyscript.f2var1 == '1'")
def func2(var_name=None, value=None):
    global seqNum

    seqNum += 1
    mySeqNum = seqNum
    log.info(f"func2 var = {var_name}, value = {value}")
    task.unique("func2")
    while 1:
        task.wait_until(state_trigger="pyscript.f2var1 == '2'")
        pyscript.f2var1 = 0
        pyscript.done = [mySeqNum, var_name]

@state_trigger("pyscript.f3var1 == '1'")
def func3(var_name=None, value=None):
    global seqNum

    seqNum += 1
    log.info(f"func3 var = {var_name}, value = {value}")
    task.unique("func2")
    pyscript.done = [seqNum, var_name]
""")

    seqNum = 0

    hass.states.async_set('pyscript.f1var1', 0)
    hass.states.async_set('pyscript.f2var1', 0)
    hass.states.async_set('pyscript.f3var1', 0)

    seqNum += 1
    # fire event to startup triggers, and handshake when they are running
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    assert eval(await wait_until_done(notifyQ)) == seqNum

    seqNum += 1
    hass.states.async_set('pyscript.f1var1', 1)
    assert eval(await wait_until_done(notifyQ)) == [seqNum, "pyscript.f1var1"]

    for i in range(5):
        seqNum += 1
        hass.states.async_set('pyscript.f1var1', 0)
        hass.states.async_set('pyscript.f1var1', 1)
        assert eval(await wait_until_done(notifyQ)) == [seqNum, "pyscript.f1var1"]

    # get func2() through wait_notify and get reply; should be in wait_notify()
    seqNum += 1
    hass.states.async_set('pyscript.f2var1', 1)
    hass.states.async_set('pyscript.f2var1', 2)
    assert eval(await wait_until_done(notifyQ)) == [seqNum, "pyscript.f2var1"]

    # now run func3() which will kill func2()
    seqNum += 1
    hass.states.async_set('pyscript.f3var1', 1)
    assert eval(await wait_until_done(notifyQ)) == [seqNum, "pyscript.f3var1"]

    # now run func3() a few more times, and also try to re-trigger func2()
    # should be no more acks from func2()
    for i in range(10):
        seqNum += 1
        hass.states.async_set('pyscript.f2var1', 2)
        hass.states.async_set('pyscript.f2var1', 0)
        hass.states.async_set('pyscript.f3var1', 0)
        hass.states.async_set('pyscript.f3var1', 1)
        assert eval(await wait_until_done(notifyQ)) == [seqNum, "pyscript.f3var1"]
