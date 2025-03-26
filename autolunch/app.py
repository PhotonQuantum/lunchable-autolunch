import datetime
import itertools
import json
import typing
from collections import OrderedDict
from typing import Optional, Literal

from lunchable import TransactionUpdateObject
from lunchable.models import (
    CategoriesObject,
    AssetsObject,
    PlaidAccountObject,
    TransactionObject,
)
from lunchable.plugins import LunchableApp
from openai import OpenAI
from pydantic import BaseModel
from rich.console import Console, Group
from rich.live import Live
from rich.live_render import LiveRender
from rich.panel import Panel

debit_system_prompt = """
You're an accounting platform, and you are trying your best to help an individual categorize her bank transactions:

1. Give a human friendly pretty payee name. Strip away transaction metadata like "Paypal", "Point of Sale", "Transfer", "MEMO", merchant code, location code, etc. Please reword and expand abbrevations if present. If you are uncertain, use "Unknown".
2. Try your best to map every merchant name to a specific category. If you are uncertain, use "Unknown".
For categorizing, you MUST put them in one of these categories:
```json
["Alcohol, Bars", "Bank Fees", "Coffee Shops", "Entertainment", "Food Delivery", "Gas, Transportation", "Gifts", "Groceries", "Income", "Payment, Transfer", "Personal Care", "Restaurants", "Ridesharing", "Self-improvement", "Shopping", "Travel", "Withdrawal", "Unknown"]
```
3. For each transaction, please give an array of two reusable matching substrings to categorize transactions from the same brand/company/person and of the same flow direction(inflow/outflow) automatically. Even if you are uncertain about the category, you MUST try your best to give general matching patterns:
	a. For the first substring,  isolate the payee name and REMOVE any potential ID CODE.
	b. For the second substring, include the GENERAL type of the transaction ONLY IF it’s helpful to decide the FLOW direction. MUST NOT include alliance info like VISA, MASTERCARD, INTERAC. MUST NOT include card type like DEBIT, CREDIT.
For each matching substring, include separators from the INPUT at the START or the END. This is to make your matching substring NOT BEING a substring of OTHER potential merchants. DO NOT add any additional separators if not present in the input.
MUST NOT generate regex or glob pattern. Just plain text substrings. Two substrings should NOT OVERLAP.

Please strictly adhere to these rules and follow a clear path of thinking, justifying your decisions. Use natural language to spell out your thoughts in detail until you are ready to give a final response. 

Your final answer MUST be A JSON ARRAY with three fields in each element: "name", "category" and "matchers”. NO markdown notation for your final answer.

Wrap your thinking process with "<think>...</think>". Wrap your final answer in "<answer>...</answer>". No other type of block is allowed in response.
"""

credit_system_prompt = """
You're an accounting platform, and you are trying your best to help an individual categorize her bank transactions:

1. Give a human friendly pretty payee name. Strip away transaction metadata like "Paypal", "Point of Sale", "Transfer", "MEMO", merchant code, location code, etc. Please reword and expand abbrevations if present. If you are uncertain, use "Unknown".
2. Try your best to map every merchant name to a specific category. If you are uncertain, use "Unknown".
For categorizing, you MUST put them in one of these categories:
```json
["Alcohol, Bars", "Bank Fees", "Coffee Shops", "Entertainment", "Food Delivery", "Gas, Transportation", "Gifts", "Groceries", "Income", "Payment, Transfer", "Personal Care", "Restaurants", "Ridesharing", "Self-improvement", "Shopping", "Travel", "Withdrawal", "Unknown"]
```
3. For each transaction, please give an array of one reusable matching substrings to categorize transactions from the same brand/company/person and of the same flow direction(inflow/outflow) automatically. Even if you are uncertain about the category, you MUST try your best to give general matching patterns: Isolate the payee name and REMOVE any potential ID CODE.
For each matching substring, include separators from the INPUT at the START or the END. This is to make your matching substring NOT BEING a substring of OTHER potential merchants. DO NOT add any additional separators if not present in the input.
MUST NOT generate regex or glob pattern. Just plain text substrings.

Please strictly adhere to these rules and follow a clear path of thinking, justifying your decisions. Use natural language to spell out your thoughts in detail until you are ready to give a final response. 

Your final answer MUST be A JSON ARRAY with three fields in each element: "name", "category" and "matchers”. NO markdown notation for your final answer.

Wrap your thinking process with "<think>...</think>". Wrap your final answer in "<answer>...</answer>". No other type of block is allowed in response.
"""

input_header = """
This is the input:
```json
"""
input_footer = """
```

<think>Let’s think step by step.
"""


class Rule(BaseModel):
    name: str
    category: str
    matchers: list[str]


class AutoLunchApp(LunchableApp):
    """
    Automatically categorize uncategorized transactions.
    """

    lunchable_models = [CategoriesObject, PlaidAccountObject, AssetsObject]
    account: Optional[PlaidAccountObject | AssetsObject]
    since: Optional[datetime.datetime]
    until: Optional[datetime.datetime]
    gpt: OpenAI
    excluded: set[str]
    console: Console
    rules: list[Rule]
    mode: Literal["debit", "credit"]

    def __init__(self, openai_api_key: str, access_token: str | None = None) -> None:
        super().__init__(access_token=access_token)
        self.gpt = OpenAI(api_key=openai_api_key)
        self.account = None
        self.excluded = set()
        self.console = Console()
        self.mode = "debit"

    def set_account(self, account: PlaidAccountObject | AssetsObject):
        """
        Set the accounts for the application.
        """
        self.account = account

    def set_mode(self, mode: Literal["debit", "credit"]):
        """
        Set the mode for the application.
        """
        self.mode = mode

    def set_since_until(
        self, since: Optional[datetime.datetime], until: Optional[datetime.datetime]
    ):
        """
        Set since and until dates for the application.
        """
        self.since = since
        self.until = until

    def add_rules(self, rules: list[Rule]):
        self.rules.extend(rules)

    def refresh_txns(self):
        match self.account:
            case PlaidAccountObject(id=id):
                self.refresh_transactions(
                    plaid_account_id=id,
                    category_id=None,
                    start_date=self.since,
                    end_date=self.until,
                )
            case AssetsObject(id=id):
                self.refresh_transactions(
                    asset_id=id,
                    category_id=None,
                    start_date=self.since,
                    end_date=self.until,
                )

    def apply_rules_to_txns(self):
        for txn in filter(
            lambda txn: txn.payee not in self.excluded and txn.category_id is None,
            self.data.transactions_list,
        ):
            matched = False
            for rule in self.rules:
                if all(matcher in txn.payee for matcher in rule.matchers):
                    category = (
                        next(
                            filter(
                                lambda c: c.name == rule.category,
                                self.data.categories_list,
                            )
                        )
                        if rule.category != "Unknown"
                        else None
                    )
                    if category is None and rule.category != "Unknown":
                        continue  # category disappeared, skip
                    category_id = category.id if category is not None else None
                    self.lunch.update_transaction(
                        txn.id,
                        TransactionUpdateObject(
                            category_id=category_id,
                            payee=rule.name if rule.name != "Unknown" else None,
                        ),
                    )
                    matched = True
                    break
            if not matched:
                yield txn

    def suggest_rules(self, count: int) -> typing.OrderedDict[str, Rule]:
        """
        Sample unclassified transactions from LunchMoney.
        """
        with self.console.status("Fetching transactions..."):
            self.refresh_txns()

            # sampled_txns: list[TransactionObject] = list(itertools.islice(
            #     filter(lambda txn: txn.id in self.excluded_id or txn.category_id is None, self.data.transactions_list),
            #     count))
            # TODO use self.data.transactions_list and separate apply with suggest after rule endpoint gets stablized
            sampled_txns: list[TransactionObject] = list(
                itertools.islice(self.apply_rules_to_txns(), count)
            )
            if not sampled_txns:
                return OrderedDict()

        txn_payee = list(set(txn.payee for txn in sampled_txns))

        gpt_text = LiveRender("")
        group = Group(
            Panel(gpt_text, height=5), self.console.status("Generating suggestions...")
        )

        resp_text = ""
        with Live(group, console=self.console, refresh_per_second=10, transient=True):
            stream = self.gpt.responses.create(
                model="gpt-4o",
                instructions=debit_system_prompt
                if self.mode == "debit"
                else credit_system_prompt,
                input=f"{input_header}{json.dumps(txn_payee)}{input_footer}",
                stream=True,
            )

            def update_text(s: str):
                text = gpt_text.renderable + s
                text = "\n".join(text.split("\n")[-5:])
                gpt_text.set_renderable(text)

            for event in stream:
                if event.type == "response.output_text.delta":
                    update_text(event.delta)
                elif event.type == "response.output_text.done":
                    resp_text = event.text.strip()
                    break
                elif event.type == "response.refusal.delta":
                    update_text(event.delta)
                elif event.type == "response.refusal.done":
                    resp_text = event.text.strip()
                    break

        if not resp_text.startswith("<think>"):
            resp_text = "<think>" + resp_text

        answer_start = resp_text.find("<answer>")
        answer_end = resp_text.find("</answer>")
        if answer_start == -1 or answer_end == -1:
            raise ValueError("No answer found in the response")

        answer_text = resp_text[answer_start + 8 : answer_end].strip()
        raw_rules = json.loads(answer_text)
        rules = [Rule(**rule) for rule in raw_rules]

        for idx, rule in enumerate(rules):
            if rule.name == "Unknown" or rule.category == "Unknown":
                self.excluded.add(txn_payee[idx])

        return OrderedDict(
            (txn_payee[idx], rule)
            for idx, rule in enumerate(rules)
            if rule.name != "Unknown" or rule.category != "Unknown"
        )
