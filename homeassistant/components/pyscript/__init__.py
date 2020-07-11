"""Component to allow running Python scripts."""

from collections import OrderedDict
import glob
import io
import logging
import os

import voluptuous as vol
import yaml

import homeassistant.components.pyscript.eval as eval
import homeassistant.components.pyscript.event as event
import homeassistant.components.pyscript.handler as handler
import homeassistant.components.pyscript.state as state
import homeassistant.components.pyscript.trigger as trigger
from homeassistant.const import (
    EVENT_HOMEASSISTANT_STARTED,
    EVENT_STATE_CHANGED,
    SERVICE_RELOAD,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.service import async_set_service_schema
from homeassistant.loader import bind_hass

_LOGGER = logging.getLogger(__name__)

DOMAIN = "pyscript"

FOLDER = "pyscripts"

CONFIG_SCHEMA = vol.Schema({DOMAIN: vol.Schema(dict)}, extra=vol.ALLOW_EXTRA)


async def async_setup(hass, config):
    """Initialize the pyscript component."""

    state.hassSet(hass)
    handler.hassSet(hass)
    trigger.hassSet(hass)
    event.hassSet(hass)

    path = hass.config.path(FOLDER)

    if not os.path.isdir(path):
        _LOGGER.error("Folder %s not found in configuration folder", FOLDER)
        return False

    triggers, services = await compile_scripts(hass)

    _LOGGER.debug("adding reload handler")

    async def reload_scripts_handler(call):
        """Handle reload service calls."""
        nonlocal triggers, services

        _LOGGER.debug(
            "stopping triggers and services, reloading scripts, and restarting"
        )
        for name, trig in triggers.items():
            await trig.stop()
        for name in services:
            hass.services.async_remove(DOMAIN, name)
        triggers, services = await compile_scripts(hass)
        for name, trig in triggers.items():
            trig.start()

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, reload_scripts_handler)

    async def state_changed(ev):
        varName = ev.data["entity_id"]
        # attr = ev.data["new_state"].attributes
        newVal = ev.data["new_state"].state
        oldVal = ev.data["old_state"].state if ev.data["old_state"] else None
        newVars = {varName: newVal, f"{varName}.old": oldVal}
        funcArgs = {
            "trigger_type": "state",
            "var_name": varName,
            "value": newVal,
            "old_value": oldVal,
        }
        await state.update(newVars, funcArgs)

    async def start_triggers(ev):
        _LOGGER.debug("adding state changed listener")
        hass.bus.async_listen(EVENT_STATE_CHANGED, state_changed)
        _LOGGER.debug("starting triggers")
        for name, trig in triggers.items():
            trig.start()

    hass.bus.async_listen(EVENT_HOMEASSISTANT_STARTED, start_triggers)

    return True


@bind_hass
async def compile_scripts(hass):
    """Compile all python scripts in FOLDER."""

    path = hass.config.path(FOLDER)

    _LOGGER.debug(f"compile_scripts: path = {path}")

    def pyscript_service_factory(name, func, symTable):
        async def pyscript_service_handler(call):
            """Handle python script service calls."""
            # ignore call.service
            _LOGGER.debug(f"service call to {name}")
            #
            # use a new AstEval context so it can run fully independently
            # of other instances (except for globalSymTable which is common)
            #
            astCtx = eval.AstEval(name, globalSymTable=symTable)
            handler.installAstFuncs(astCtx)
            funcArgs = {
                "trigger_type": "service",
            }
            funcArgs = funcArgs.update(call.data)
            handler.create_task(func.call(astCtx, [], call.data))

        return pyscript_service_handler

    triggers = {}
    services = set()
    for file in glob.iglob(os.path.join(path, "*.py")):
        name = os.path.splitext(os.path.basename(file))[0]
        _LOGGER.debug(f"parsing {file}")
        with open(file) as fd:
            source = fd.read()

        globalSymTable = {}
        astCtx = eval.AstEval(name, globalSymTable=globalSymTable)
        handler.installAstFuncs(astCtx)
        if not astCtx.parse(source, filename=file):
            continue
        await astCtx.eval()

        for name, func in globalSymTable.items():
            _LOGGER.debug(f"globalSymTable got {name}, {func}")
            if not isinstance(func, eval.EvalFunc):
                continue
            if name == SERVICE_RELOAD:
                _LOGGER.error(
                    f"function '{name}' in {file} conflicts with {SERVICE_RELOAD} service; ignoring (please rename)"
                )
                continue
            desc = func.getDocString()
            if desc is None or desc == "":
                desc = f"pyscript function {name}()"
            desc = desc.lstrip(" \n\r")
            if desc.startswith("yaml"):
                try:
                    desc = desc[4:].lstrip(" \n\r")
                    fd = io.StringIO(desc)
                    service_desc = (
                        yaml.load(fd, Loader=yaml.BaseLoader) or OrderedDict()
                    )
                    fd.close()
                except Exception as exc:
                    _LOGGER.error(
                        "Unable to decode yaml doc_string for %s(): %s", name, str(exc)
                    )
                    raise HomeAssistantError(exc)
            else:
                fields = OrderedDict()
                for arg in func.getPositionalArgs():
                    fields[arg] = OrderedDict(description=f"argument {arg}")
                service_desc = {"description": desc, "fields": fields}

            trigArgs = {}
            trigDecorators = {
                "time_trigger",
                "state_trigger",
                "event_trigger",
                "state_active",
                "time_active",
            }
            for dec in func.getDecorators():
                decName, decArgs = dec[0], dec[1]
                if decName in trigDecorators:
                    if decName not in trigArgs:
                        trigArgs[decName] = []
                    if decArgs is not None:
                        trigArgs[decName] += decArgs
                elif decName == "service":
                    if decArgs is not None:
                        _LOGGER.error(
                            "%s defined in %s: decorator @service takes no arguments; ignored",
                            name,
                            file,
                        )
                        continue
                    _LOGGER.debug(
                        f"registering {DOMAIN}/{name} (service_desc = {service_desc}"
                    )
                    hass.services.async_register(
                        DOMAIN,
                        name,
                        pyscript_service_factory(name, func, globalSymTable),
                    )
                    async_set_service_schema(hass, DOMAIN, name, service_desc)
                    services.add(name)
                else:
                    _LOGGER.warning(
                        "%s defined in %s has unknown decorator @%s",
                        name,
                        file,
                        decName,
                    )
            for decName in trigDecorators:
                if decName in trigArgs and len(trigArgs[decName]) == 0:
                    trigArgs[decName] = None

            argCheck = {
                "state_trigger": {1},
                "state_active": {1},
                "event_trigger": {1, 2},
            }
            for decName, argCnt in argCheck.items():
                if decName not in trigArgs or trigArgs[decName] is None:
                    continue
                if len(trigArgs[decName]) not in argCnt:
                    _LOGGER.error(
                        "%s defined in %s decorator @%s got %d argument%s, expected %s; ignored",
                        name,
                        file,
                        decName,
                        len(trigArgs[decName]),
                        "s" if len(trigArgs[decName]) > 1 else "",
                        " or ".join(sorted(argCnt)),
                    )
                    del trigArgs[decName]
                if argCnt == 1:
                    trigArgs[decName] = trigArgs[decName][0]

            if len(trigArgs) > 0:
                trigArgs["action"] = func
                trigArgs["actionAstCtx"] = eval.AstEval(
                    name, globalSymTable=globalSymTable
                )
                handler.installAstFuncs(trigArgs["actionAstCtx"])
                trigArgs["globalSymTable"] = globalSymTable
                triggers[name] = trigger.TrigInfo(name, trigArgs)

    return triggers, services
