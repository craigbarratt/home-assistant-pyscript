# Home Assistant Pyscript - Python Scripting Component

This integration allows you to write Python functions and scripts that can implement a wide range of
automation, logic and triggers. State variables are bound to Python variables, and services are
callable as Python functions, so it's easy and concise to implement logic.

Functions you write can be configured to be called as a service or run upon time, state-change or
event triggers.  Functions can also call any service, fire events and set state variables.
Functions can sleep or wait for additional changes in state variables or events, without slowing or
affecting other operations.  You can think of these functions as small programs that run in
parallel, independently of each other, and they could be active for extended periods of time.

State, event and time triggers are specified by Python function decorators (the "@" lines
immediately before each function definition).  A state trigger can be any Python expression using
state variables - the trigger is evaluated only when a state variable it references changes, and the
trigger occurs when the expression is true or non-zero.  A time trigger could be a single event (eg:
date and time), a repetitive event (eg: at a particular time each day or weekday, or daily relative
to sunrise or sunset, or any regular time period within an optional range), or using cron syntax
(where events occur periodically based on a concise specification of ranges of minutes, hours, days
of week, days of month and months).  An event trigger specifies the event type, and an optional
Python trigger test based on the event data that runs the Python function if true.

Pyscript implements a Python interpreter using the ast parser output, in a fully async manner.
That allows several of the "magic" features to be implemented in a seamless Pythonesque manner, such
as binding of variables to states, and functions to services.  Pyscript supports imports, although
the valid import list is restricted for security reasons.  Pyscript does not (yet) support some
language features like declaring new objects, try/except, eval, and some syntax like "with".
Pyscript provides a handful of additional built-in functions that connect to Hass features, like
logging, accessing state variables as strings (if you need to compute their names dynamically),
sleeping, and waiting for triggers.

Pyscript provides functionality that complements the existing automations, templates, and triggers.
It presents a simplified and more integrated binding for Python scripting than
[Python Scripts](https://www.home-assistant.io/integrations/python_script), which
provides direct access to Hass internals.

## Directory tree

`homeassistant/components/pyscript` contains the component source code

`tests/components/pyscript` contains test code

`home-assistant.io/source/_integrations` contains the
[documentation](https://github.com/craigbarratt/home-assistant-pyscript/blob/master/home-assistant.io/source/_integrations/pyscript.markdown) in markdown format.

## Copyright

Copyright (C) 2020 Craig Barratt. All rights reserved.

This program is free software; you can redistribute it and/or modify it under the terms of the Apache 2.0 License.

See the LICENSE file.
