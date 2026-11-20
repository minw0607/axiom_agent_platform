# ruff: noqa: E501 - the documents' sentences are data, kept one per line
"""Generate synthetic model documentation packages for app 2, with known ground truth.

Each package documents one fictional model in three documents: a development report, a data
description and a monitoring plan. Each scenario leaves out a section, leaves one as a
placeholder, or writes one that is present but weak. The ground truth records, for every
checklist item, whether the generator wrote the section, where, and what a reviewer should
find, computed here from what was written, independently of the app's section parser.

All models, teams and figures are fictional, written for this repository.
Output: data/synthetic/model_docs/<package_id>/ and data/synthetic/model_docs/catalog.json.
Re-running produces identical files.

    uv run python scripts/generate_model_docs.py
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "synthetic" / "model_docs"
CHECKLIST = ROOT / "config" / "apps" / "model_review" / "checklist.yaml"
PLACEHOLDER = ["To be completed in the next release."]
DOCUMENTS = {
    "development_report": "Development report",
    "data_description": "Data description",
    "monitoring_plan": "Monitoring plan",
}


@dataclass
class Model:
    package_id: str
    model_name: str
    model_type: str
    scenario: str
    # Checklist item -> the section's lines. Missing items are not written; PLACEHOLDER
    # items are written with a heading and one line, so code finds them empty.
    sections: dict[str, list[str]]
    # What a reviewer should find, for the golden set: item -> expected finding.
    expected: dict[str, str] = field(default_factory=dict)


def _common(name: str, use: str, personal_data: str) -> dict[str, list[str]]:
    """Sections most models share, worded for this model. Each covers every point its
    checklist item expects, so only a scenario's own sections are weak (step 3b)."""
    return {
        "MR-7": [
            f"{name} output is reviewed by an analyst before any action is taken.",
            "Analysts can override the output and must record a reason for each override.",
            "Overrides are reported monthly to the model owner and reviewed for patterns.",
        ],
        "MR-9": [
            "Records with missing values above 5% in any input field were reviewed by hand.",
            "After that review, every input field was at least 98% complete.",
            "Duplicate records (0.4% of the sample) were removed before training.",
            f"The sample covers every region and product the model is used for: {use}.",
        ],
        "MR-10": [
            f"The personal data the model uses is limited to {personal_data}.",
            "The training data contains no names, addresses or account numbers.",
            "Customer identifiers are replaced with random keys before the data leaves the source system.",
            "Access to the modelling data set is limited to the development team and logged.",
        ],
        "MR-12": [
            "Any change to inputs, method or thresholds is logged and reviewed before release.",
            "The model owner approves each change, and the model risk team approves material ones.",
            "A material change is validated again before it is used.",
            "Incidents, such as a data feed failure, are reported to the model owner within one business day.",
            "The owner records each incident and its fix in the model inventory.",
        ],
    }


def models() -> list[Model]:
    out: list[Model] = []

    # mdl-01: complete and well documented.
    name = "Synthetic Model 01"
    s = _common(
        name, "all retail deposit products", "each customer's deposit balances, product and tenure"
    )
    s |= {
        "MR-1": [
            f"{name} forecasts monthly run-off of retail deposit balances for liquidity planning.",
            "It is used by the treasury team to size the liquidity buffer each month.",
            "It is not used for pricing or for decisions about individual customers.",
        ],
        "MR-2": [
            "The model assumes rate changes of at most 200 basis points within a year.",
            "Forecasts beyond 12 months are outside its tested range and are not used.",
            "Deposit products launched after the training period are mapped to the nearest existing product.",
        ],
        "MR-3": [
            "A segmented survival model estimates the share of balances remaining each month.",
            "Segments are product, customer tenure band and balance band (24 segments).",
            "A survival model was chosen over a regression because balances leave gradually.",
            "The rate sensitivity is a single coefficient per segment, estimated on 2015 to 2024 data.",
        ],
        "MR-4": [
            "The model was tested out of time on 2024 data that was not used to fit it.",
            "Mean absolute error of the 12-month forecast was 2.1% of balances.",
            "The forecast stayed within the 3.0% tolerance set by treasury in 11 of 12 months.",
            "Results were compared with the previous model, whose error was 3.4%.",
        ],
        "MR-5": [
            "Forecasts were recomputed with rates 100 and 200 basis points higher and lower.",
            "Run-off rose by at most 4.5% of balances under the largest rise.",
            "A stress scenario with a one-month outflow of 10% was run, and the buffer covered it.",
        ],
        "MR-6": [
            "The model forecasts total balances, not outcomes for individual customers.",
            "No decision about a customer uses its output, so no group of customers is scored.",
            "The owner will reassess this if the output is ever used for individual offers.",
        ],
        "MR-8": [
            "Monthly balances come from the deposit ledger, extracted on the first business day.",
            "Rates come from the published rate history maintained by treasury.",
            "Each extract is reconciled to the general ledger total before use.",
            "There is no label delay: actual balances are known at each month end.",
        ],
        "MR-11": [
            "Forecast error is measured each month against actual balances.",
            "An error above 3.0% for two consecutive months triggers a review by the owner.",
            "An error above 5.0% in any month is escalated to the model risk committee.",
            "Segment shares are compared with the training data each quarter for drift.",
        ],
    }
    out.append(Model("mdl-01", f"{name} (deposit run-off forecast)", "forecast", "complete", s))

    # mdl-02: no monitoring metrics and no change management (one critical gap).
    name = "Synthetic Model 02"
    s = _common(
        name,
        "card transactions in all channels",
        "each cardholder's transaction history and card details",
    )
    del s["MR-12"]
    s |= {
        "MR-1": [
            f"{name} scores card transactions for fraud risk in real time.",
            "Transactions above the alert threshold are queued for a fraud analyst.",
            "The score does not block a transaction on its own.",
        ],
        "MR-2": [
            "The model was trained on card-present and online transactions only.",
            "Wallet payments, introduced after the training period, are scored as online transactions.",
            "Fraud patterns change quickly, so the model is expected to degrade without retraining.",
            "It was tested on card-present and online transactions from 2024.",
            "Scores for wallet payments should not be relied on until wallet payments are tested.",
        ],
        "MR-3": [
            "A gradient boosted tree model with 140 input features.",
            "Features describe the transaction, the card's recent history and the merchant.",
            "Hyperparameters were chosen by cross-validation on the 2023 data.",
            "Boosted trees were chosen over a logistic regression because they caught more fraud value at the same alert volume.",
        ],
        "MR-4": [
            "Tested out of time on six months of 2024 transactions.",
            "At the alert threshold, the model catches 71% of fraud value with 2.5 alerts per fraud case.",
            "The previous rules-based system caught 58% at the same alert volume.",
        ],
        "MR-5": [
            "Performance was measured separately for each channel and for transaction size bands.",
            "Detection in the smallest size band was 49%, the lowest of any band.",
            "Removing each of the ten strongest features in turn lowered detection by at most 6 points.",
            "A stress scenario with fraud attempts doubled in one week kept alerts within team capacity.",
        ],
        "MR-6": [
            "Alert rates were compared across age bands and regions on the test data.",
            "Alert rates differed by at most 0.3 percentage points between groups.",
            "The model uses no protected characteristic as an input.",
        ],
        "MR-8": [
            "Transactions come from the card authorization system, extracted daily.",
            "Each daily extract is reconciled to the authorization count before use.",
            "Fraud labels come from confirmed chargebacks, which arrive up to 90 days later.",
            "Labels for the last 90 days of the sample were therefore excluded.",
        ],
    }
    out.append(
        Model(
            "mdl-02",
            f"{name} (card fraud alerting)",
            "classifier",
            "no_monitoring_metrics",
            s,
            expected={
                "MR-11": "No monitoring metrics or thresholds: a fast-degrading fraud model has no trigger for review.",
                "MR-12": "No change management or incident process.",
            },
        )
    )

    # mdl-03: affects customers but has no fairness assessment.
    name = "Synthetic Model 03"
    s = _common(
        name,
        "consumer loans past due",
        "each borrower's loan balance, payment and contact history, and age band",
    )
    s |= {
        "MR-1": [
            f"{name} ranks past-due consumer loans for the collections team to contact first.",
            "Customers ranked highest are called within two days; others receive a letter.",
            "The ranking decides the order and channel of contact for each customer.",
            "It must not be used for credit limits, pricing or decisions to sell a debt.",
        ],
        "MR-2": [
            "The model covers unsecured consumer loans only.",
            "It was trained during a period of low unemployment and may rank differently in a downturn.",
            "Accounts in hardship programs are excluded and handled by a separate team.",
        ],
        "MR-3": [
            "A logistic regression predicts the chance an account is paid up within 30 days without contact.",
            "Accounts least likely to cure on their own are ranked first.",
            "Inputs are days past due, balance, payment history, contact history and customer age band.",
            "A logistic regression was chosen over boosted trees so that collectors can see why an account ranks high.",
        ],
        "MR-4": [
            "Tested out of time on accounts that went past due in the second half of 2024.",
            "The area under the ROC curve was 0.78 on the test data and 0.80 on the training data.",
            "Calibration by decile was within 2 percentage points of observed cure rates.",
            "The owner's acceptance threshold was an area under the curve of at least 0.75.",
        ],
        "MR-5": [
            "The ranking was recomputed with each input shifted by one standard deviation.",
            "Days past due and payment history had the largest effect on rank.",
            "Ranks were stable: 90% of accounts moved by fewer than 5 places in 100.",
            "A downturn scenario with unemployment 3 points higher was run; the top decile kept 85% of its accounts.",
        ],
        "MR-8": [
            "Account data comes from the loan servicing system, extracted daily.",
            "Contact history comes from the collections dialer log.",
            "Both sources are matched on the account number, then the number is replaced by a key.",
            "Each daily extract is reconciled to the servicing system's count of past-due accounts.",
            "The cure label is known 30 days after an account goes past due, so the last 30 days are excluded.",
        ],
        "MR-11": [
            "The cure rate of each decile is compared with the forecast every month.",
            "A difference above 5 percentage points in any decile triggers a review.",
            "The share of accounts in each age band is tracked monthly against the training data.",
            "A change above 3 percentage points in any age band's share triggers a review.",
        ],
    }
    out.append(
        Model(
            "mdl-03",
            f"{name} (collections prioritization)",
            "classifier",
            "no_fairness_assessment",
            s,
            expected={
                "MR-6": "No fairness assessment, although the ranking decides how each customer is contacted and uses customer age band as an input.",
            },
        )
    )

    # mdl-04: performance tested in sample only; sensitivity section is a placeholder.
    name = "Synthetic Model 04"
    s = _common(
        name,
        "fixed-rate residential mortgages",
        "each loan's balance, rate, age and payment history",
    )
    s |= {
        "MR-1": [
            f"{name} forecasts prepayment speeds of fixed-rate mortgages for hedging.",
            "The forecast is used by the treasury hedging desk to set the size of the interest rate hedge each quarter.",
            "It is not used for decisions about individual borrowers.",
        ],
        "MR-2": [
            "The model covers fixed-rate loans with terms of 15 to 30 years.",
            "Refinancing incentives beyond 300 basis points were not observed in the data.",
            "Loans from a portfolio acquired in 2023 are assumed to behave like originated loans.",
            "Forecasts for rate moves beyond 300 basis points should not be relied on.",
        ],
        "MR-3": [
            "A multinomial logit model of monthly prepayment, by loan age and rate incentive.",
            "Seasonality is captured by a month-of-year factor.",
            "Burnout is modelled by the number of past months with a refinancing incentive.",
            "A logit model was chosen over a survival model because it separates refinancing from other prepayment.",
        ],
        "MR-4": [
            "The model fits the 2015 to 2024 history closely.",
            "The fitted prepayment rate is within 0.2% of the actual rate in every year of that history.",
            "R-squared of the fitted monthly rates is 0.94.",
        ],
        "MR-5": PLACEHOLDER,
        "MR-6": [
            "The forecast is for the portfolio as a whole and makes no decision about a borrower.",
            "No group of borrowers is scored or treated differently because of it.",
            "The owner will reassess this if the model is used at the loan level.",
        ],
        "MR-8": [
            "Loan data comes from the mortgage servicing system, extracted monthly.",
            "Market rates come from the published rate history used across the bank.",
            "Acquired loans come from the seller's tape, converted to the same fields.",
            "Each monthly extract is reconciled to the servicing system's loan count and balance.",
            "There is no label delay: prepayments are known at each month end.",
        ],
        "MR-11": [
            "Actual and forecast prepayment speeds are compared every quarter.",
            "A difference above 2 percentage points of annual speed triggers a review.",
            "The hedge ratio is recalculated whenever the model is re-estimated.",
        ],
    }
    out.append(
        Model(
            "mdl-04",
            f"{name} (mortgage prepayment)",
            "forecast",
            "in_sample_testing_only",
            s,
            expected={
                "MR-4": "Performance is reported only on the data the model was fitted to; no out-of-time or holdout test.",
                "MR-5": "Sensitivity and stress testing is a placeholder.",
            },
        )
    )

    # mdl-05: no data lineage; privacy section is a placeholder.
    name = "Synthetic Model 05"
    s = _common(
        name,
        "existing customers with a current account",
        "each customer's account activity and past product holdings",
    )
    s["MR-10"] = ["TBD"]
    s |= {
        "MR-1": [
            f"{name} predicts which customers are likely to open a savings account.",
            "Customers above the cut-off receive a savings offer by email.",
            "The offer is the same for everyone who receives it.",
            "The marketing team uses the output; it must not be used for credit or pricing decisions.",
        ],
        "MR-2": [
            "The model was trained on customers who had a current account for at least a year.",
            "Newer customers are not scored.",
            "It predicts interest in the offer, not whether the product suits the customer.",
            "It was tested only on customers from the training period, not on a later period.",
        ],
        "MR-3": [
            "A random forest with 60 inputs on account activity and past product holdings.",
            "The cut-off was set so that 10% of eligible customers receive the offer.",
            "Inputs were chosen from 300 candidates by their importance on a validation sample.",
            "A random forest was chosen over a logistic regression because it ranked responders better on the validation sample.",
        ],
        "MR-4": [
            "Tested on a holdout of 20% of customers, drawn at random from the same period.",
            "Customers in the top decile opened accounts at 3.2 times the average rate.",
            "The marketing team's benchmark was at least 2.5 times the average rate.",
            "The model was not tested on a later period.",
        ],
        "MR-5": [
            "Response rates were compared for customers with high and low balances.",
            "The top decile's response rate fell to 2.1 times average among low-balance customers.",
            "No stress scenario was run, as the model drives marketing offers only.",
        ],
        "MR-6": [
            "Offer rates were compared across age bands and regions.",
            "Customers over 70 received offers at half the average rate.",
            "The owner judged this acceptable because the offer is optional.",
        ],
        "MR-11": [
            "The response rate of each decile is compared with the forecast every quarter.",
            "A top-decile response rate below twice the average triggers a review.",
            "The share of customers above the cut-off is tracked monthly.",
            "A change above 2 percentage points in that share triggers a review.",
        ],
    }
    s["MR-9"] = [
        "The sample is all eligible customers at the end of 2023.",
        "Customers who closed their accounts during 2023 are not in the sample.",
        "Activity fields were complete for 97% of customers.",
        "No duplicate customers were found after matching on the customer key.",
    ]
    out.append(
        Model(
            "mdl-05",
            f"{name} (savings offer propensity)",
            "classifier",
            "no_data_lineage",
            s,
            expected={
                "MR-8": "No data sources or lineage: the inputs cannot be traced to their systems.",
                "MR-10": "Personal data and privacy section is a placeholder, although the model scores individual customers.",
                "MR-6": "Customers over 70 receive offers at half the average rate; the documentation accepts this without analysis.",
            },
        )
    )

    # mdl-06: a generative AI assistant with no limitations and no human oversight section.
    name = "Synthetic Model 06"
    s = _common(
        name,
        "calls to the customer service center",
        "the call transcript and the customer's record identifier",
    )
    del s["MR-7"]
    s |= {
        "MR-1": [
            f"{name} drafts a summary of each customer service call from its transcript.",
            "The summary is saved to the customer's record for the next agent to read.",
            "It uses a hosted large language model with a fixed prompt.",
            "Customer service agents read the summaries; they must not be used to decide complaints.",
        ],
        "MR-3": [
            "The call transcript and a fixed instruction are sent to a hosted language model.",
            "The instruction asks for the reason for the call, actions taken and open items.",
            "No model was trained; the prompt was refined over 40 example calls.",
            "A hosted model was chosen over training one because no labelled summaries existed.",
        ],
        "MR-4": [
            "The team read 40 summaries and judged them accurate.",
            "Agents in a pilot said the summaries saved time.",
            "No error rate was measured.",
        ],
        "MR-5": [
            "Summaries were produced for very short and very long calls.",
            "Calls longer than 45 minutes were cut to fit the model's input limit.",
            "Summaries of cut calls left out the end of the call.",
        ],
        "MR-6": [
            "Summaries were read for calls in English only.",
            "Calls in other languages are transcribed but were not tested.",
            "No comparison across customer groups was made.",
        ],
        "MR-8": [
            "Transcripts come from the call recording system's speech-to-text service.",
            "Transcripts are sent to the hosted model and not stored by the provider.",
            "The prompt is versioned in the team's repository.",
            "Transcripts are extracted after each call and matched to the call log daily.",
            "There is no label; agent flags, known within a week, stand in for one.",
        ],
        "MR-11": [
            "Agents can flag a summary as wrong with one click.",
            "The flag rate is reported weekly.",
            "A flag rate above 5% in any week triggers a review of the prompt.",
        ],
    }
    out.append(
        Model(
            "mdl-06",
            f"{name} (call summary assistant)",
            "generative",
            "no_oversight_or_limitations",
            s,
            expected={
                "MR-2": "No limitations section, although long calls are cut and non-English calls were not tested.",
                "MR-7": "No human oversight section: summaries are saved to customer records with no review.",
                "MR-4": "Performance rests on reading 40 summaries; no error rate was measured.",
                "MR-6": "Only English calls were read and no comparison across customer groups was made, so the fairness question is not answered.",
            },
        )
    )
    return out


def render(
    model: Model, kind: str, items: list[dict[str, object]]
) -> tuple[str, dict[str, dict[str, object]]]:
    """One document's text, and where each checklist section was written in it."""
    lines = [f"# {DOCUMENTS[kind]}: {model.model_name}", "", f"Model type: {model.model_type}", ""]
    where: dict[str, dict[str, object]] = {}
    number = 0
    for item in items:
        if item["document"] != kind or item["id"] not in model.sections:
            continue
        number += 1
        body = model.sections[str(item["id"])]
        lines.append(f"## {number}. {item['title']}")
        start = len(lines) + 1
        lines += body
        where[str(item["id"])] = {"heading_line": start - 1, "lines": [start, len(lines)]}
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n", where


def ground_truth(
    model: Model,
    items: list[dict[str, object]],
    placed: dict[str, dict[str, object]],
    min_lines: int,
) -> dict[str, object]:
    checks = {}
    for item in items:
        iid = str(item["id"])
        body = model.sections.get(iid)
        if body is None:
            outcome = "missing"
        elif len([x for x in body if x.strip()]) < min_lines:
            outcome = "empty"
        else:
            outcome = "present"
        entry: dict[str, object] = {"outcome": outcome, "document": item["document"]}
        if iid in placed:
            entry |= placed[iid]
        checks[iid] = entry
    present = sum(c["outcome"] == "present" for c in checks.values())
    return {
        "package_id": model.package_id,
        "scenario": model.scenario,
        "checks": checks,
        "completeness": f"{present}/{len(items)}",
        "critical_gaps": [
            str(i["id"])
            for i in items
            if i["critical"] and checks[str(i["id"])]["outcome"] != "present"
        ],
        "expected_findings": model.expected,
    }


def main() -> None:
    checklist = yaml.safe_load(CHECKLIST.read_text())
    items: list[dict[str, object]] = checklist["items"]
    OUT.mkdir(parents=True, exist_ok=True)
    catalog = []
    for model in models():
        folder = OUT / model.package_id
        (folder / "documents").mkdir(parents=True, exist_ok=True)
        documents, placed = [], {}
        for kind, title in DOCUMENTS.items():
            text, where = render(model, kind, items)
            (folder / "documents" / f"{kind}.md").write_text(text)
            doc_id = f"{model.package_id}-{kind}"
            documents.append({"doc_id": doc_id, "kind": kind, "title": title, "file": f"{kind}.md"})
            placed |= {k: {"doc_id": doc_id, **v} for k, v in where.items()}
        meta = {
            "package_id": model.package_id,
            "model_name": model.model_name,
            "model_type": model.model_type,
            "scenario": model.scenario,
            "documents": documents,
        }
        (folder / "package.json").write_text(json.dumps(meta, indent=2) + "\n")
        truth = ground_truth(model, items, placed, int(checklist["min_lines"]))
        (folder / "ground_truth.json").write_text(json.dumps(truth, indent=2) + "\n")
        catalog.append({k: meta[k] for k in ("package_id", "model_name", "model_type", "scenario")})
    (OUT / "catalog.json").write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"Wrote {len(catalog)} packages to {OUT}")


if __name__ == "__main__":
    main()
