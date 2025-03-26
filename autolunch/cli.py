import json
import os
from datetime import datetime
from typing import Optional

import click
import questionary
from lunchable.models import AssetsObject
from pydantic import TypeAdapter

from autolunch.app import AutoLunchApp, Rule


def text_truncate_pad(s: str, length: int) -> str:
    if len(s) > length:
        return s[:(length - 3) // 2] + "..." + s[-(length - 3) // 2:]
    else:
        return s.ljust(length)


@click.group
def autolunch():
    """
    Automatically classify uncategorized transactions using GPT.
    """
    pass


@autolunch.command
@click.option(
    "--ruleset",
    type=click.STRING,
    help="Path to the ruleset file",
    default="ruleset.json",
)
@click.option(
    "--openai-api-key",
    type=click.STRING,
    help=(
            "OpenAI API Key - defaults to the "
            "OPENAI_API_KEY environment variable"
    ),
    envvar="OPENAI_API_KEY",
)
@click.option(
    "-t",
    "--token",
    "access_token",
    type=click.STRING,
    help=(
            "LunchMoney Access Token - defaults to the "
            "LUNCHMONEY_ACCESS_TOKEN environment variable"
    ),
    envvar="LUNCHMONEY_ACCESS_TOKEN",
)
@click.option(
    "--since",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help=(
            "Start date for transactions - defaults to "
            "the beginning of the current month"
    )
)
@click.option(
    "--until",
    type=click.DateTime(formats=["%Y-%m-%d"]),
    default=None,
    help=(
            "End date for transactions - defaults to "
            "the current date"
    )
)
def classify(ruleset: str, openai_api_key: str, access_token: str, since: Optional[datetime],
             until: Optional[datetime]):
    """
    Classify uncategorized transactions.
    """
    app = AutoLunchApp(openai_api_key=openai_api_key, access_token=access_token)
    app.set_since_until(since, until)
    app.refresh_data()

    if not os.path.exists(ruleset):
        with open(ruleset, "w") as f:
            json.dump([], f)

    with open(ruleset, "r") as f:
        s = f.read()
        app.rules = TypeAdapter(list[Rule]).validate_json(s)

    choices = [
        questionary.Choice(title=[
            ("class:text", text_truncate_pad(asset.display_name if asset.display_name else asset.name, 30)),
            ("class:text", "   "),
            ("class:type",
             text_truncate_pad("[" + (asset.type_name if isinstance(asset, AssetsObject) else asset.type) + "]", 10)),
            ("class:text", "   "),
            ("class:balance", "$" + str(asset.balance)),
        ], value=asset)
        for asset in app.data.asset_map.values()
    ]
    style = questionary.Style([
        ("type", "fg:#008b8b"),
        ("balance", "fg:#008000")]
    )
    account = questionary.select("Which account do you want to classify transactions for?", choices=choices,
                                 style=style).ask()
    if not account:
        return
    app.set_account(account)

    mode = questionary.select("Is this a credit or debit account?", choices=["Credit", "Debit"]).ask()
    app.set_mode(mode.lower())

    while rules := app.suggest_rules(20):
        choices = [
            questionary.Choice(title=[
                ("class:text", text_truncate_pad(original, 60)),
                ("class:text", "   "),
                ("class:emph", text_truncate_pad(rule.name, 30)),
                ("class:text", "   "),
                ("class:emph", text_truncate_pad(rule.category, 30)),
                ("class:text", "   "),
                ("class:pattern", text_truncate_pad(str(rule.matchers), 60)),
            ], value=rule, checked=rule.name != "Unknown" and rule.category != "Unknown")
            for original, rule in rules.items()
        ]
        style = questionary.Style([
            ("emph", "fg:#008b8b"),
            ("pattern", "fg:#808080")]
        )
        checked = questionary.checkbox("Select rules to apply", choices=choices, style=style).ask()
        if checked is None:
            exit_or_rerun = questionary.select("Would you like to rerun or exit the rule suggestion?", ["Rerun", "Exit"]).ask()
            if exit_or_rerun == "Exit":
                break
        else:
            app.add_rules(checked)
            with open(ruleset, "w") as f:
                json.dump([rule.model_dump() for rule in app.rules], f)


if __name__ == "__main__":
    autolunch()
