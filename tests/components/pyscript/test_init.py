"""Test the pyscript component."""
from ast import literal_eval
import asyncio
from datetime import datetime as dt

from homeassistant.components.pyscript import DOMAIN
import homeassistant.components.pyscript.trigger as trigger
from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.helpers.service import async_get_all_descriptions
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


async def test_setup_fails_on_no_dir(hass, caplog):
    """Test we fail setup when no dir found."""
    with patch("homeassistant.components.pyscript.os.path.isdir", return_value=False):
        res = await async_setup_component(hass, "pyscript", {})

    assert not res
    assert "Folder pyscripts not found in configuration folder" in caplog.text


async def test_service_exists(hass):
    """Test discover, compile script and install a service."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@service
def func1():
    pass

def func2():
    pass
""",
    )
    assert hass.services.has_service("pyscript", "func1")
    assert hass.services.has_service("pyscript", "reload")
    assert not hass.services.has_service("pyscript", "func2")


async def test_service_description(hass):
    """Test service description defined in doc_string."""

    await setup_script(
        hass,
        None,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """
@service
def func_no_doc_string(param1=None):
    pass

@service
def func_simple_doc_string(param2=None, param3=None):
    \"\"\"This is func2_simple_doc_string.\"\"\"
    pass

@service
def func_yaml_doc_string(param2=None, param3=None):
    \"\"\"yaml
description: This is func_yaml_doc_string.
fields:
  param1:
    description: first argument
    example: 12
  param2:
    description: second argument
    example: 34
\"\"\"
    pass
""",
    )
    descriptions = await async_get_all_descriptions(hass)

    assert descriptions[DOMAIN]["func_no_doc_string"] == {
        "description": "pyscript function func_no_doc_string()",
        "fields": {"param1": {"description": "argument param1"}},
    }

    assert descriptions[DOMAIN]["func_simple_doc_string"] == {
        "description": "This is func2_simple_doc_string.",
        "fields": {
            "param2": {"description": "argument param2"},
            "param3": {"description": "argument param3"},
        },
    }

    assert descriptions[DOMAIN]["func_yaml_doc_string"] == {
        "description": "This is func_yaml_doc_string.",
        "fields": {
            "param1": {"description": "first argument", "example": "12"},
            "param2": {"description": "second argument", "example": "34"},
        },
    }


async def test_service_run(hass, caplog):
    """Test running a service with keyword arguments."""
    notifyQ = asyncio.Queue(0)
    await setup_script(
        hass,
        notifyQ,
        dt(2020, 7, 1, 11, 59, 59, 999999),
        """

@service
def func1(arg1=1, arg2=2):
    x = 1
    x = 2 * x + 3
    log.info(f"this is func1 x = {x}, arg1 = {arg1}, arg2 = {arg2}")
    pyscript.done = [x, arg1, arg2]

@service
def func2(**kwargs):
    x = 1
    x = 2 * x + 3
    log.info(f"this is func1 x = {x}, kwargs = {kwargs}")
    pyscript.done = [x, kwargs]

""",
    )
    await hass.services.async_call("pyscript", "func1", {})
    v = await wait_until_done(notifyQ)
    assert literal_eval(v) == [5, 1, 2]
    assert "this is func1 x = 5" in caplog.text

    await hass.services.async_call("pyscript", "func1", {"arg1": "string1"})
    v = await wait_until_done(notifyQ)
    assert literal_eval(v) == [5, "string1", 2]

    await hass.services.async_call(
        "pyscript", "func1", {"arg1": "string1", "arg2": 123}
    )
    v = await wait_until_done(notifyQ)
    assert literal_eval(v) == [5, "string1", 123]

    await hass.services.async_call("pyscript", "func2", {})
    v = await wait_until_done(notifyQ)
    assert literal_eval(v) == [5, {}]

    await hass.services.async_call("pyscript", "func2", {"arg1": "string1"})
    v = await wait_until_done(notifyQ)
    assert literal_eval(v) == [5, {"arg1": "string1"}]

    await hass.services.async_call(
        "pyscript", "func2", {"arg1": "string1", "arg2": 123}
    )
    v = await wait_until_done(notifyQ)
    assert literal_eval(v) == [5, {"arg1": "string1", "arg2": 123}]
