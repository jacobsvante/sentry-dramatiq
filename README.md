# sentry-dramatiq

[![Travis CI build status (Linux)](https://travis-ci.org/jmagnusson/sentry-dramatiq.svg?branch=master)](https://travis-ci.org/jmagnusson/sentry-dramatiq)
[![PyPI version](https://img.shields.io/pypi/v/sentry-dramatiq.svg)](https://pypi.python.org/pypi/sentry-dramatiq/)
[![License](https://img.shields.io/pypi/l/sentry-dramatiq.svg)](https://pypi.python.org/pypi/sentry-dramatiq/)
[![Available as wheel](https://img.shields.io/pypi/wheel/sentry-dramatiq.svg)](https://pypi.python.org/pypi/sentry-dramatiq/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/sentry-dramatiq.svg)](https://pypi.python.org/pypi/sentry-dramatiq/)
[![PyPI status (alpha/beta/stable)](https://img.shields.io/pypi/status/sentry-dramatiq.svg)](https://pypi.python.org/pypi/sentry-dramatiq/)
[![Coverage Status](https://coveralls.io/repos/github/jmagnusson/sentry-dramatiq/badge.svg?branch=master)](https://coveralls.io/github/jmagnusson/sentry-dramatiq?branch=master)

[Dramatiq task processor](https://dramatiq.io/) integration for the [Sentry SDK](https://docs.sentry.io/error-reporting/quickstart/?platform=python).

## Installation

```
pip install sentry-dramatiq
```

## Setup

```python
import sentry_sdk
import sentry_dramatiq

sentry_sdk.init(
    '__DSN__',
    integrations=[sentry_dramatiq.DramatiqIntegration()],
)
```

## Features

- Tags Sentry events with the message ID as `dramatiq_message_id`
- Adds all info about a Dramatiq message to a separate context (shows up as its own section in the Sentry UI)

## Known limitations

- `sentry_sdk.init()` has to be called before broker is initialized as the integration monkey patches `Broker.__init__`
