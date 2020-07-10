"""Implements all the trigger logic."""

import asyncio
import datetime
import locale
import logging
import math
import time
import traceback
import re

import homeassistant.helpers.sun as sun
import homeassistant.components.pyscript.event as event
import homeassistant.components.pyscript.eval as eval
import homeassistant.components.pyscript.handler as handler
import homeassistant.components.pyscript.state as state
from homeassistant.util import dt as dt_util
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET


_LOGGER = logging.getLogger(__name__)


hass = None


async def wait_until(astCtx, state_trigger=None, state_check_now=True, time_trigger=None, event_trigger=None, timeout=None, **kwargs):
    """Implements task.wait_until()."""
    if state_trigger is None and time_trigger is None and event_trigger is None:
        if timeout is not None:
            await asynio.sleep(timeout)
            return {"trigger_type": "timeout"}
        else:
            return {"trigger_type": "none"}
    stateTrigIdent = None
    stateTrigExpr = None
    eventTrigExpr = None
    notifyQ = asyncio.Queue(0)
    if state_trigger is not None:
        stateTrigExpr = eval.AstEval(f"{astCtx.name} wait_until state_trigger", astCtx.globalSymTable)
        handler.installAstFuncs(stateTrigExpr)
        stateTrigExpr.parse(state_trigger)
        #
        # check straight away to see if the condition is met (to avoid race conditions)
        #
        if await stateTrigExpr.eval():
            return {"trigger_type": "state"}
        stateTrigIdent = stateTrigExpr.astGetNames()
        _LOGGER.debug(f"trigger {astCtx.name} wait_until: watching vars {stateTrigIdent}")
        if len(stateTrigIdent) > 0:
            state.notifyAdd(stateTrigIdent, notifyQ)
    if event_trigger is not None:
        if isinstance(event_trigger, str):
            event_trigger = [event_trigger]
        event.notifyAdd(event_trigger[0], notifyQ)
        if len(event_trigger) > 1:
            eventTrigExpr = eval.AstEval(f"trigger {astCtx.name} wait_until event_trigger", astCtx.globalSymTable)
            handler.installAstFuncs(eventTrigExpr)
            eventTrigExpr.parse(event_trigger[1])
    t0 = time.monotonic()
    while 1:
        thisTimeout = None
        if time_trigger is not None:
            now = dt_now()
            timeNext = TimerTriggerNext(time_trigger, now)
            _LOGGER.debug(f"trigger {astCtx.name} wait_until timeNext = {timeNext}, now = {now}")
            if timeNext is not None:
                thisTimeout = (timeNext - now).total_seconds()
        if timeout is not None:
            timeLeft = t0 + timeout - time.monotonic()
            if timeLeft <= 0:
                ret = {"trigger_type": "timeout"}
                break
            if thisTimeout is None or thisTimeout > timeLeft:
                thisTimeout = timeLeft
        if thisTimeout is None:
            if state_trigger is None and event_trigger is None:
                _LOGGER.debug(f"trigger {astCtx.name} wait_until no next time - returning with none")
                return {"trigger_type": "none"}
            _LOGGER.debug(f"trigger {astCtx.name} wait_until no timeout")
            notifyType, notifyInfo = await notifyQ.get()
        else:
            try:
                _LOGGER.debug(f"trigger {astCtx.name} wait_until {thisTimeout} secs")
                notifyType, notifyInfo = await asyncio.wait_for(notifyQ.get(), timeout=thisTimeout)
            except asyncio.TimeoutError:
                ret = {"trigger_type": "time"}
                break
        if notifyType == "state":
            newVars = notifyInfo[0] if notifyInfo else None
            if stateTrigExpr is None or await stateTrigExpr.eval(newVars):
                ret = notifyInfo[1] if notifyInfo else None
                break
        elif notifyType == "event":
            if eventTrigExpr is None or await eventTrigExpr.eval(notifyInfo):
                ret = notifyInfo
                break
        else:
            _LOGGER.error(f"trigger {astCtx.name} wait_until got unexpected queue message {notifyType}")

    if stateTrigIdent:
        for name in stateTrigIdent:
            state.notifyDel(name, notifyQ)
    if event_trigger is not None:
        event.notifyDel(event_trigger[0], notifyQ)
    _LOGGER.debug(f"trigger {astCtx.name} wait_until returning {ret}")
    return ret


def wait_until_factory(astCtx):
    """Factory that adds the function execution context to the task.wait_until() function."""
    async def wait_until_call(*arg, **kw):
        return await wait_until(astCtx, *arg, **kw)
    return wait_until_call


astFunctions = {
    "task.wait_until": wait_until_factory,
}


#
# Mappings of day of week name to number, using US convention of sun is 0.
# Initialized based on locale at startup.
#
dow2int = {
}


def hassSet(h):
    """Initialize hass handle, localize dow, register functions."""
    global hass

    hass = h

    for i in range(0, 7):
        dow2int[locale.nl_langinfo(getattr(locale, f"ABDAY_{i+1}")).lower()] = i
        dow2int[locale.nl_langinfo(getattr(locale, f"DAY_{i+1}")).lower()] = i
    _LOGGER.debug(f"initialized dow2int = {dow2int}")

    handler.registerAst(astFunctions)


def isleap(y):
    """Returns True or False if y is a leap year."""
    return (y % 4) == 0 and (y % 100) != 0 or (y % 400) == 0


def dt_now():
    """Returns current time."""
    return datetime.datetime.now()


def daysInMon(m, y):
    """Returns numbers of days in month m of year y, 1 <= m <= 12."""
    dom = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

    m -= 1
    if m < 0 or m >= len(dom):
        return -1
    if (m == 1) and isleap(y):
        return dom[m] + 1
    else:
        return dom[m]


def daysBetween(m1, d1, y1, m2, d2, y2):
    """Calculate the number of days in between m1/d1/y1 and m2/d2/y2."""
    d = datetime.date(y2, m2, d2) - datetime.date(y1, m1, d1)
    return round(d.days)


def cronGE(cron, fld, curr):
    """Returns the next value >= curr that matches cron[fld],
where cron is the 5 element cron definition.  If nothing
matches then the smallest element is returned."""
    min_ge = 1000
    min = 1000

    if cron[fld] == "*":
        return curr
    for elt in cron[fld].split(","):
        r = elt.split("-")
        if len(r) == 2:
            n0, n1 = [int(r[0]), int(r[1])]
            if n0 < min:
                min = n0
            if n0 > n1:
                # wrap case
                if curr >= n0 or curr < n1:
                    return curr
            else:
                if n0 <= curr and curr <= n1:
                    return curr
                if curr <= n0 and n0 < min_ge:
                    min_ge = n0
        elif len(r) == 1:
            n0 = int(r[0])
            if curr == n0:
                return curr
            if n0 < min:
                min = n0
            if curr <= n0 and n0 < min_ge:
                min_ge = n0
        else:
            _LOGGER.warning(f"can't parse field {elt} in cron entry {cron[fld]}")
            return curr
    if min_ge < 1000:
        return min_ge
    return min


def parseTimeOffset(s):
    """Parse a time offset."""
    m = re.split(r"([-+]?\s*\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(\w*)", s)
    scale = 1
    value = 0
    if len(m) == 4:
        value = float(m[1].replace(" ", ""))
        #
        # TODO: need i18n for these strings
        #
        if m[2] == "m" or m[2] == "min" or m[2] == "minutes":
            scale = 60
        elif m[2] == "h" or m[2] == "hr" or m[2] == "hours":
            scale = 60 * 60
        elif m[2] == "d" or m[2] == "day" or m[2] == "days":
            scale = 60 * 60 * 24
        elif m[2] == "w" or m[2] == "week" or m[2] == "weeks":
            scale = 60 * 60 * 24 * 7
    return value * scale


def parseDateTime(dateTimeStr, dayOffset, now):
    """Parse a date time string, returning datetime."""
    global dow2int

    year = now.year
    month = now.month
    day = now.day

    s = dateTimeStr.strip().lower()
    #
    # parse the date
    #
    skip = True
    m0 = re.split(r"^(\d+)[-/](\d+)(?:[-/](\d+))?", s)
    m1 = re.split(r"^(\w+).*", s)
    if len(m0) == 5:
        year, month, day = int(m0[1]), int(m0[2]), int(m0[3])
        dayOffset = 0   # explicit date means no offset
    elif len(m0) == 4:
        month, day = int(m0[1]), int(m0[2])
        dayOffset = 0   # explicit date means no offset
    elif len(m1) == 3:
        if m1[1] in dow2int:
            dow = dow2int[m1[1]]
            if dow >= (now.isoweekday() % 7):
                dayOffset = dow - (now.isoweekday() % 7)
            else:
                dayOffset = 7 + dow - (now.isoweekday() % 7)
        elif m1[1] == "today":
            dayOffset = 0
        elif m1[1] == "tomorrow":
            dayOffset = 1
        else:
            skip = False
    else:
        skip = False
    if dayOffset != 0:
        now = datetime.datetime(year, month, day) + datetime.timedelta(days=dayOffset)
        year = now.year
        month = now.month
        day = now.day
    else:
        now = datetime.datetime(year, month, day)
    if skip:
        i = s.find(" ")
        if i >= 0:
            s = s[i+1:].strip()
        else:
            return now

    #
    # parse the time
    #
    skip = True
    m0 = re.split(r"(\d+):(\d+)(?::(\d*\.?\d+(?:[eE][-+]?\d+)?))?", s)
    if len(m0) == 5:
        if m0[3] is not None:
            hour, min, sec = int(m0[1]), int(m0[2]), float(m0[3])
        else:
            hour, min, sec = int(m0[1]), int(m0[2]), 0
    elif s.startswith("sunrise") or s.startswith("sunset"):
        #
        # TODO: need i18n for these strings
        #
        if s.startswith("sunrise"):
            t = sun.get_astral_event_date(hass, SUN_EVENT_SUNRISE)
        else:
            t = sun.get_astral_event_date(hass, SUN_EVENT_SUNSET)
        if t is None:
            _LOGGER.warning(f"'{s}' not defined at this latitude")
            # return something in the past so it is ignored
            return now - datetime.timedelta(days=100)
        t = dt_util.as_local(t)
        hour, min, sec = t.hour, t.minute, t.second
        _LOGGER.debug(f"trigger: got {s} = {hour:02d}:{min:02d}:{sec:02d} (t = {t})")
    elif s.startswith("noon"):
        hour, min, sec = 12, 0, 0
    elif s.startswith("midnight"):
        hour, min, sec = 0, 0, 0
    else:
        hour, min, sec = 0, 0, 0
        skip = False
    now = now + datetime.timedelta(seconds=sec + 60 * (min + 60 * hour))
    if skip:
        i = s.find(" ")
        if i >= 0:
            s = s[i+1:].strip()
        else:
            return now
    #
    # parse the offset
    #
    if len(s) > 0 and (s[0] == '+' or s[0] == '-'):
        now = now + datetime.timedelta(seconds=parseTimeOffset(s))
    return now


def TimerActiveCheck(timeSpec, now):
    """Check if the given time matches the time specification."""
    posCheck = False
    posCnt = 0
    negCheck = True

    for entry in timeSpec if isinstance(timeSpec, list) else [timeSpec]:
        thisMatch = False
        neg = False
        a = entry.strip()
        if a.startswith("not"):
            neg = True
            a = a[3:].strip()
        else:
            posCnt = posCnt + 1
        m0 = re.split(r"cron\((\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\)", a)
        m1 = re.split(r"range\(([^,]*),(.*)\)", a)
        if len(m0) == 7:
            cron = m0[1:6]
            check = [now.minute, now.hour, now.day, now.month, now.isoweekday() % 7]
            thisMatch = True
            for fld in range(5):
                if check[fld] != cronGE(cron, fld, check[fld]):
                    thisMatch = False
                    break
        elif len(m1) == 4:
            start = parseDateTime(m1[1].strip(), 0, now)
            end = parseDateTime(m1[2].strip(), 0, start)
            if start < end:
                if start <= now and now <= end:
                    thisMatch = True
            else:
                if start <= now or now <= end:
                    thisMatch = True
        else:
            thisMatch = False

        if neg:
            negCheck = negCheck and not thisMatch
        else:
            posCheck = posCheck or thisMatch
    #
    # An empty spec, or only neg specs, matches True
    #
    if posCnt == 0:
        posCheck = True
    return posCheck and negCheck


def TimerTriggerNext(timeSpec, now):
    """Return the next trigger time based on the given time and time specification."""
    nextTime = None
    for a in timeSpec if isinstance(timeSpec, list) else [timeSpec]:
        m0 = re.split(r"cron\((\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\)", a)
        m1 = re.split(r"once\((.*)\)", a)
        m2 = re.split(r"period\(([^,]*),([^,]*)(?:,([^,]*))?\)", a)
        if len(m0) == 7:
            cron = m0[1:6]
            yearNext = now.year
            minNext = cronGE(cron, 0, now.minute)
            monNext = cronGE(cron, 3, now.month)              # 1-12
            mdayNext = cronGE(cron, 2, now.day)               # 1-31
            wdayNext = cronGE(cron, 4, now.isoweekday() % 7)  # 0-6
            today = True
            if ((cron[2] == "*" and (now.isoweekday() % 7) != wdayNext)
                    or (cron[4] == "*" and now.day != mdayNext)
                    or (now.day != mdayNext and (now.isoweekday() % 7) != wdayNext)
                    or (now.month != monNext)):
                today = False
            m = now.minute + 1
            if (now.hour + 1) <= cronGE(cron, 1, now.hour):
                m = 0
            minNext = cronGE(cron, 0, m % 60)

            carry = minNext < m
            h = now.hour
            if carry:
                h = h + 1
            hrNext = cronGE(cron, 1, h % 24)
            carry = hrNext < h

            if carry or not today:
                # this event occurs after today

                minNext = cronGE(cron, 0, 0)
                hrNext = cronGE(cron, 1, 0)

                #
                # calculate the date of the next occurance of this event, which
                # will be on a different day than the current
                #

                # check monthly day specification
                d1 = now.day + 1
                day1 = cronGE(cron, 2, (d1 - 1) % daysInMon(now.month, now.year) + 1)
                carry1 = day1 < d1

                # check weekly day specification
                d2 = (now.isoweekday() % 7) + 1
                wdayNext = cronGE(cron, 4, d2 % 7)
                if wdayNext < d2:
                    daysAhead = 7 - d2 + wdayNext
                else:
                    daysAhead = wdayNext - d2
                day2 = (d1 + daysAhead - 1) % daysInMon(now.month, now.year) + 1
                carry2 = day2 < d1

                #
                # based on their respective specifications, day1, and day2 give
                # the day of the month for the next occurance of this event.
                #
                if cron[2] == "*" and cron[4] != "*":
                    day1 = day2
                    carry1 = carry2
                if cron[2] != "*" and cron[4] == "*":
                    day2 = day1
                    carry2 = carry1

                if (carry1 and carry2) or (now.month != monNext):
                    # event does not occur in this month
                    if now.month == 12:
                        monNext = cronGE(cron, 3, 1)
                        yearNext = yearNext + 1
                    else:
                        monNext = cronGE(cron, 3, now.month + 1)
                    # recompute day1 and day2
                    day1 = cronGE(cron, 2, 1)
                    db = daysBetween(now.month, now.day, now.year, monNext, 1, yearNext) + 1
                    wd = ((now.isoweekday() % 7) + db) % 7
                    # wd is the day of the week of the first of month mon
                    wdayNext = cronGE(cron, 4, wd)
                    if wdayNext < wd:
                        day2 = 1 + 7 - wd + wdayNext
                    else:
                        day2 = 1 + wdayNext - wd
                    if cron[2] != "*" and cron[4] == "*":
                        day2 = day1
                    if cron[2] == "*" and cron[4] != "*":
                        day1 = day2
                    if day1 < day2:
                        mdayNext = day1
                    else:
                        mdayNext = day2
                else:
                    # event occurs in this month
                    monNext = now.month
                    if not carry1 and not carry2:
                        if day1 < day2:
                            mdayNext = day1
                        else:
                            mdayNext = day2
                    elif not carry1:
                        mdayNext = day1
                    else:
                        mdayNext = day2

                #
                # now that we have the min, hr, day, mon, yr of the next event,
                # figure out what time that turns out to be.
                # TODO was:
                # secNext = 60 * (minNext - math.floor(minNext))
                # minNext = math.floor(minNext)
                t = datetime.datetime(yearNext, monNext, mdayNext, hrNext, minNext, 0)
                if now < t and (nextTime is None or t < nextTime):
                    nextTime = t
            else:
                # this event occurs today
                secs = 3600 * (hrNext - now.hour) + 60 * (minNext - now.minute) - now.second - 1e-6 * now.microsecond
                t = now + datetime.timedelta(seconds=secs)
                if now < t and (nextTime is None or t < nextTime):
                    nextTime = t

        elif len(m1) == 3:
            t = parseDateTime(m1[1].strip(), 0, now)
            if t <= now:
                #
                # Try tomorrow (won't make a difference if spec has full date)
                #
                t = parseDateTime(m1[1].strip(), 1, now)
            if now < t and (nextTime is None or t < nextTime):
                nextTime = t

        elif len(m2) == 5:
            start = parseDateTime(m2[1].strip(), 0, now)
            if now < start and (nextTime is None or start < nextTime):
                nextTime = start
            period = parseTimeOffset(m2[2].strip())
            if now >= start and period > 0:
                # TODO was t = start + period * (1 + math.floor((now - start) / period))
                secs = period * (1.0 + math.floor((now - start).total_seconds() / period))
                t = start + datetime.timedelta(seconds=secs)
                if m2[3] is None:
                    if now < t and (nextTime is None or t < nextTime):
                        nextTime = t
                else:
                    end = parseDateTime(m2[3].strip(), 0, now)
                    if end < start:
                        #
                        # end might be a time tomorrow
                        #
                        end = parseDateTime(m2[3].strip(), 1, now)
                    if now < t and t <= end and (nextTime is None or t < nextTime):
                        nextTime = t
                    if nextTime is None or now >= end:
                        #
                        # Try tomorrow's start (won't make a difference if spec has
                        # full date)
                        #
                        start = parseDateTime(m2[3].strip(), 1, now)
                        if now < start and (nextTime is None or start < nextTime):
                            nextTime = start
        else:
            _LOGGER.warning(f"Can't parse {a} in timeTrigger check")
    return nextTime


class TrigInfo():
    """Class for all trigger-decorated functions."""

    def __init__(self, name, trigCfg):
        self.name = name
        self.trigCfg = trigCfg
        self.stateTrigger = trigCfg.get("state_trigger", None)
        self.timeTrigger = trigCfg.get("time_trigger", None)
        self.eventTrigger = trigCfg.get("event_trigger", None)
        self.stateActive = trigCfg.get("state_active", None)
        self.timeActive = trigCfg.get("time_active", None)
        self.action = trigCfg.get("action")
        self.actionAstCtx = trigCfg.get("actionAstCtx")
        self.globalSymTable = trigCfg.get("globalSymTable", {})
        self.notifyQ = asyncio.Queue(0)
        self.activeExpr = None
        self.stateTrigExpr = None
        self.stateTrigIdent = None
        self.eventTrigExpr = None
        self.haveTrigger = False

        _LOGGER.debug(f"trigger {self.name} eventTrigger = {self.eventTrigger}")

        if self.stateActive is not None:
            self.activeExpr = eval.AstEval(f"trigger {self.name} state_active", self.globalSymTable)
            handler.installAstFuncs(self.activeExpr)
            self.activeExpr.parse(self.stateActive)

        if self.timeTrigger is not None:
            self.haveTrigger = True

        if self.stateTrigger is not None:
            self.stateTrigExpr = eval.AstEval(f"trigger {self.name} state_trigger", self.globalSymTable)
            handler.installAstFuncs(self.stateTrigExpr)
            self.stateTrigExpr.parse(self.stateTrigger)
            self.stateTrigIdent = self.stateTrigExpr.astGetNames()
            _LOGGER.debug(f"trigger {self.name}: watching vars {self.stateTrigIdent}")
            if len(self.stateTrigIdent) > 0:
                state.notifyAdd(self.stateTrigIdent, self.notifyQ)
            self.haveTrigger = True

        if self.eventTrigger is not None:
            _LOGGER.debug(f"trigger {self.name} adding eventTrigger {self.eventTrigger[0]}")
            event.notifyAdd(self.eventTrigger[0], self.notifyQ)
            if len(self.eventTrigger) == 2:
                self.eventTrigExpr = eval.AstEval(f"trigger {self.name} event_trigger", self.globalSymTable)
                handler.installAstFuncs(self.eventTrigExpr)
                self.eventTrigExpr.parse(self.eventTrigger[1])
            self.haveTrigger = True


    async def stop(self):
        """Stop this trigger task."""

        if self.stateTrigIdent:
            state.notifyDel(self.stateTrigIdent, self.notifyQ)
        if self.eventTrigger is not None:
            event.notifyDel(self.eventTrigger[0], self.notifyQ)
        if self.task:
            try:
                self.task.cancel()
                await self.task
            except asyncio.CancelledError:
                pass
        _LOGGER.debug(f"trigger {self.name} is stopped")

    def start(self):
        """Start this trigger task."""
        self.task = handler.create_task(self.triggerWatch())
        _LOGGER.debug(f"trigger {self.name} is active")

    async def triggerWatch(self):
        """Task that runs for each trigger, waiting for the next trigger and calling the function."""

        while 1:
            try:
                timeout = None
                notifyInfo = None
                notifyType = None
                if self.timeTrigger:
                    now = dt_now()
                    timeNext = TimerTriggerNext(self.timeTrigger, now)
                    _LOGGER.debug(f"trigger {self.name} timeNext = {timeNext}, now = {now}")
                    if timeNext is not None:
                        timeout = (timeNext - now).total_seconds()
                if timeout is None and self.haveTrigger:
                    _LOGGER.debug(f"trigger {self.name} waiting for state change or event")
                    notifyType, notifyInfo = await self.notifyQ.get()
                elif timeout is not None:
                    try:
                        _LOGGER.debug(f"trigger {self.name} waiting for {timeout} secs")
                        notifyType, notifyInfo = await asyncio.wait_for(self.notifyQ.get(), timeout=timeout)
                    except asyncio.TimeoutError:
                        notifyInfo = {"trigger_type": "time"}
                        if ((not self.activeExpr or await self.activeExpr.eval())
                                and (not self.timeActive or TimerActiveCheck(self.timeActive, dt_now()))
                                and self.action):
                            _LOGGER.debug(f"trigger {self.name} got timeTrigger, running action")
                            handler.create_task(self.action.call(self.actionAstCtx, kwargs=notifyInfo))
                        else:
                            _LOGGER.debug(f"trigger {self.name} got timeTrigger, but not active")
                        continue
                if notifyType == "state" or notifyType is None:
                    if notifyInfo:
                        newVars, funcArgs = notifyInfo
                    else:
                        newVars, funcArgs = {}, {}
                    if ((self.stateTrigExpr is None or await self.stateTrigExpr.eval(newVars))
                            and (self.activeExpr is None or await self.activeExpr.eval(newVars))
                            and (self.timeActive is None or TimerActiveCheck(self.timeActive, dt_now()))
                            and self.action):
                        _LOGGER.debug(f'trigger {self.name} stateTrigExpr = {await self.stateTrigExpr.eval(newVars) if self.stateTrigExpr else None} based on {newVars}')
                        _LOGGER.debug(f"trigger {self.name} got stateTrigExpr, running action (kwargs = {funcArgs})")
                        handler.create_task(self.action.call(self.actionAstCtx, kwargs=funcArgs))
                    else:
                        _LOGGER.debug(f"trigger {self.name} got stateTrigExpr, but not active")
                        # _LOGGER.debug(f'stateTrigExpr = {await self.stateTrigExpr.eval(newVars) if self.stateTrigExpr else None}')
                        # _LOGGER.debug(f'timerActive = {TimerActiveCheck(self.timeActive, dt_now())
                        #                                                       if self.timeActive else None}')
                elif notifyType == "event":
                    if ((self.eventTrigExpr is None or await self.eventTrigExpr.eval(notifyInfo))
                            and (self.activeExpr is None or await self.activeExpr.eval(notifyInfo))
                            and (self.timeActive is None or TimerActiveCheck(self.timeActive, dt_now()))
                            and self.action):
                        _LOGGER.debug(f"trigger {self.name} got eventTrigExpr, running action (kwargs = {notifyInfo})")
                        handler.create_task(self.action.call(self.actionAstCtx, kwargs=notifyInfo))
                    else:
                        _LOGGER.debug(f"trigger {self.name} got eventTrigExpr, but not active")
                elif notifyType is not None:
                    _LOGGER.error(f"trigger {self.name} got unexpected queue message {notifyType}")

                #
                # if there is no time, event or state trigger, then quit
                # (empty triggers mean run the function once at startup)
                #
                if self.stateTrigger is None and self.timeTrigger is None and self.eventTrigger is None:
                    _LOGGER.debug(f"trigger {self.name} returning")
                    return
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.error(f"{self.name}: " + traceback.format_exc(-1))
                if self.stateTrigIdent:
                    state.notifyDel(self.stateTrigIdent, self.notifyQ)
                if self.eventTrigger is not None:
                    event.notifyDel(self.eventTrigger[0], self.notifyQ)
                return
