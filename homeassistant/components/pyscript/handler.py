"""Function call handling."""

import asyncio
import logging
import traceback

_LOGGER = logging.getLogger(__name__)


hass = None


async def async_sleep(duration):
    """Implement task.sleep()."""
    await asyncio.sleep(float(duration))


async def event_fire(eventType, eventData={}):
    """Implement event.fire()."""
    hass.bus.async_fire(eventType, eventData)


UniqueTask2Name = {}
UniqueName2Task = {}


async def task_unique(name, kill_me=False):
    """Implement task.unique()."""
    global UniqueTask2Name, UniqueName2Task
    task = current_task()
    if name in UniqueName2Task:
        if not kill_me:
            task = UniqueName2Task[name]
        try:
            task.cancel()
            await task
        except asyncio.CancelledError:
            pass
        UniqueTask2Name.pop(UniqueName2Task[name], None)
        UniqueName2Task.pop(name, None)
    UniqueName2Task[name] = task
    UniqueName2Task[task] = name


def service_has_service(domain, name):
    """Implement service.has_service()."""
    return hass.services.has_service(domain, name)


async def service_call(domain, name, **kwargs):
    """Implement service.call()."""
    await hass.services.async_call(domain, name, **kwargs)


#
# We create loggers for each top-level function that include
# that function's name.  We cache them here so we only create
# one for each function
#
Loggers = {}


def getLogger(astCtx, type, *arg, **kw):
    """Return a logger function tied to the execution context of a function."""
    global Loggers

    if astCtx.name not in Loggers:
        #
        # Maintain a cache for efficiency.  Remove last name (handlers)
        # and replace with "func.{name}".
        #
        name = __name__
        i = name.rfind(".")
        if i >= 0:
            name = f"{name[0:i]}.func.{astCtx.name}"
        Loggers[astCtx.name] = logging.getLogger(name)
    return getattr(Loggers[astCtx.name], type)


def getLoggerDebug(astCtx, *arg, **kw):
    """Implement log.debug()."""
    return getLogger(astCtx, "debug", *arg, **kw)


def getLoggerError(astCtx, *arg, **kw):
    """Implement log.error()."""
    return getLogger(astCtx, "error", *arg, **kw)


def getLoggerInfo(astCtx, *arg, **kw):
    """Implement log.info()."""
    return getLogger(astCtx, "info", *arg, **kw)


def getLoggerWarning(astCtx, *arg, **kw):
    """Implement log.warning()."""
    return getLogger(astCtx, "warning", *arg, **kw)


functions = {
    "event.fire": event_fire,
    "task.sleep": async_sleep,
    "task.unique": task_unique,
    "service.call": service_call,
    "service.has_service": service_has_service,
}


#
# Functions that take the AstEval context as a first argument,
# which is needed by a handful of special functions that need the
# ast context
#
astFunctions = {
    "log.debug": getLoggerDebug,
    "log.error": getLoggerError,
    "log.info": getLoggerInfo,
    "log.warning": getLoggerWarning,
}


def hassSet(h):
    """Initialize hass handle."""
    global hass
    hass = h


def register(funcs):
    """Register functions to be available for calling."""
    global functions

    for name, func in funcs.items():
        functions[name] = func


def deregister(*names):
    """Deregister functions."""
    global functions

    for name in names:
        if name in functions:
            del functions[name]


def registerAst(funcs):
    """Register functions that need ast context to be available for calling."""
    global astFunctions

    for name, func in funcs.items():
        astFunctions[name] = func


def deregisterAst(*names):
    """Deregister functions that need ast context."""
    global astFunctions

    for name in names:
        if name in astFunctions:
            del astFunctions[name]


def installAstFuncs(astCtx):
    """Install ast functions into the local symbol table."""
    symTable = {}
    for name, func in astFunctions.items():
        symTable[name] = func(astCtx)
    astCtx.setLocalSymTable(symTable)


def get(name):
    """Lookup a function locally and then as a service."""
    func = functions.get(name, None)
    if func:
        return func
    s = name.split(".", 1)
    if len(s) != 2:
        return None
    domain = s[0]
    service = s[1]
    if not hass.services.has_service(domain, service):
        return None

    async def serviceCall(*args, **kwargs):
        await hass.services.async_call(domain, service, kwargs)

    return serviceCall


def current_task():
    """Return our asyncio current task."""
    try:
        # python >= 3.7
        return asyncio.current_task()
    except AttributeError:
        # python <= 3.6
        return asyncio.tasks.Task.current_task()


async def runCoro(coro):
    """Run coroutine task and update Unique task on start and exit."""
    global UniqueTask2Name, UniqueName2Task
    try:
        await coro
    except asyncio.CancelledError:
        task = current_task()
        if task in UniqueTask2Name:
            UniqueName2Task.pop(UniqueTask2Name[task], None)
            UniqueTask2Name.pop(task, None)
        raise
    except Exception:
        _LOGGER.error("runCoro: " + traceback.format_exc(-1))
    task = current_task()
    if task in UniqueTask2Name:
        UniqueName2Task.pop(UniqueTask2Name[task], None)
        UniqueTask2Name.pop(task, None)


def create_task(coro):
    """Create a new task that runs a coroutine."""
    return hass.loop.create_task(runCoro(coro))
