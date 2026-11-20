# Monitoring plan: Synthetic Model 05 (savings offer propensity)

Model type: classifier

## 1. Monitoring metrics and thresholds
The response rate of each decile is compared with the forecast every quarter.
A top-decile response rate below twice the average triggers a review.
The share of customers above the cut-off is tracked monthly.
A change above 2 percentage points in that share triggers a review.

## 2. Change management and incidents
Any change to inputs, method or thresholds is logged and reviewed before release.
The model owner approves each change, and the model risk team approves material ones.
A material change is validated again before it is used.
Incidents, such as a data feed failure, are reported to the model owner within one business day.
The owner records each incident and its fix in the model inventory.
