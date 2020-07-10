"""Handles state variable access and change notification."""

import logging

import homeassistant.components.pyscript.handler as handler

_LOGGER = logging.getLogger(__name__)

#
# notify message queues by variable
#
Notify = {}

#
# Last value of state variable notifications.  We maintain this
# so that trigger evaluation can use the last notified value,
# rather than fetching the current value, which is subject to
# race conditions multiple state variables are set.
#
NotifyVarLast = {}


hass = None


def notifyAdd(varNames, queue):
    """Register to notify state variables changes to be sent to queue."""
    global Notify

    for varName in varNames if isinstance(varNames, list) else [varNames]:
        s = varName.split(".")
        if len(s) != 2 and len(s) != 3:
            continue
        v = f"{s[0]}.{s[1]}"
        if v not in Notify:
            Notify[v] = {}
        Notify[v][queue] = varNames


def notifyDel(varNames, queue):
    """Unregister notify of state variables changes for given queue."""
    global Notify

    for varName in varNames if isinstance(varNames, list) else [varNames]:
        s = varName.split(".")
        if len(s) != 2 and len(s) != 3:
            continue
        v = f"{s[0]}.{s[1]}"
        if v not in Notify or queue not in Notify[v]:
            return
        del Notify[v][queue]


async def update(vars, funcArgs):
    """Deliver all notifications for state variable changes."""
    global Notify, NotifyVarLast

    _LOGGER.debug(f"state.update({vars}, {funcArgs})")
    notify = {}
    for varName, varVal in vars.items():
        if varName in Notify:
            NotifyVarLast[varName] = varVal
            notify.update(Notify[varName])

    for q, varNames in notify.items():
        try:
            await q.put(["state", [notifyVarGet(varNames), funcArgs]])
        except Exception as err:
            _LOGGER.error(f"notify Q put failed: {err}")


def notifyVarGet(varNames):
    """Return the most recent value of a state variable change."""
    vars = {}
    for varName in varNames if varNames is not None else []:
        if varName in NotifyVarLast:
            vars[varName] = NotifyVarLast[varName]
    return vars


def set(varName, value, attributes=None):
    """Set a state variable and optional attributes in hass."""
    if len(varName.split(".")) != 2:
        _LOGGER.error(f"invalid variable name {varName} (should be 'domain.entity')")
        return
    _LOGGER.debug(f"setting {varName} = {value}, attr = {attributes}")
    hass.states.async_set(varName, value, attributes)

#
# Check if a State variable exists.  Variables are of the form domain.entity
# or domain.entity.attribute.
#
def exist(varName):
    """Check if a state variable value or attribute exists in hass."""
    s = varName.split(".")
    if len(s) != 2 and len(s) != 3:
        return False
    value = hass.states.get(f"{s[0]}.{s[1]}")
    if value and (len(s) == 2 or value.attributes.get(s[2]) is not None):
        return True
    else:
        return False


def get(varName):
    """Get a state variable value or attribute from hass."""
    s = varName.split(".")
    if len(s) != 2 and len(s) != 3:
        return None
    value = hass.states.get(f"{s[0]}.{s[1]}")
    if not value:
        return None
    if len(s) == 2:
        _LOGGER.debug(f"state.get {varName} = {value.state}")
        return value.state
    return value.attributes.get(s[2])


functions = {
    "state.get":     lambda *arg, **kw: get(*arg, **kw),
    "state.set":     lambda *arg, **kw: set(*arg, **kw),
}


def hassSet(h):
    """Initialize hass handle and register built-ins."""
    global hass
    hass = h
    handler.register(functions)
