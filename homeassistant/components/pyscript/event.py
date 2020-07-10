"""Handles event firing and notification."""

import logging

_LOGGER = logging.getLogger(__name__)


hass = None


def hassSet(h):
    """Initialize hass handle."""
    global hass
    hass = h


#
# notify message queues by event type
#
Notify = {}
NotifyRemove = {}

async def event_listener(ev):
    """Listen callback for given event which updates any notifications."""
    _LOGGER.debug(f"event_listener({ev})")
    funcArgs = {
        "trigger_type": "event",
        "event_type": ev.event_type,
    }
    funcArgs.update(ev.data)
    await update(ev.event_type, funcArgs)


def notifyAdd(eventType, queue):
    """Register to notify for events of given type to be sent to queue."""
    global Notify

    if eventType not in Notify:
        Notify[eventType] = set()
        _LOGGER.debug(f"event.notifyAdd({eventType}) -> adding event listener")
        NotifyRemove[eventType] = hass.bus.async_listen(eventType, event_listener)
    Notify[eventType].add(queue)


def notifyDel(eventType, queue):
    """Unregister to notify for events of given type for given queue."""
    global Notify

    if eventType not in Notify or queue not in Notify[eventType]:
        return
    Notify[eventType].discard(queue)
    if len(Notify[eventType]) == 0:
        NotifyRemove[eventType]()
        _LOGGER.debug(f"event.notifyDel({eventType}) -> removing event listener")
        del NotifyRemove[eventType]


async def update(eventType, funcArgs):
    """Deliver all notifications for an event of the given type."""
    global Notify

    _LOGGER.debug(f"event.update({eventType}, {vars}, {funcArgs})")
    notify = set()
    if eventType in Notify:
        for q in Notify[eventType]:
            try:
                await q.put(["event", funcArgs])
            except Exception as err:
                _LOGGER.error(f"notify Q put failed: {err}")
