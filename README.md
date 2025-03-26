# lunchable-autolunch

Automatically rename merchant names and autocategorise transactions for LunchMoney.

## Installation

```shell
pip install lunchable-autolunch
```

## Usage

```shell
export LUNCHMONEY_ACCESS_TOKEN=...
export OPENAI_API_KEY=...
lunchable plugins autolunch classify
```

## Missing Lunch Money API

The Lunch Money API is missing a few key features that would make this plugin more useful:
- Rule API (and the ability to create multiple patterns on the same field/regex support)
- Filter out uncategorised transactions