# Monitoring plan: Synthetic Model 03 (collections prioritization)

Model type: classifier

## 1. Monitoring metrics and thresholds
The cure rate of each decile is compared with the forecast every month.
A difference above 5 percentage points in any decile triggers a review.
The share of accounts in each age band is tracked monthly against the training data.

## 2. Change management and incidents
Any change to inputs, method or thresholds is logged and reviewed before release.
A material change is validated again before it is used.
Incidents, such as a data feed failure, are reported to the model owner within one business day.
The owner records each incident and its fix in the model inventory.
