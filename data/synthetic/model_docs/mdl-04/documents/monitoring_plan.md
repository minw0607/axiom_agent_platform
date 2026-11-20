# Monitoring plan: Synthetic Model 04 (mortgage prepayment)

Model type: forecast

## 1. Monitoring metrics and thresholds
Actual and forecast prepayment speeds are compared every quarter.
A difference above 2 percentage points of annual speed triggers a review.
The hedge ratio is recalculated whenever the model is re-estimated.

## 2. Change management and incidents
Any change to inputs, method or thresholds is logged and reviewed before release.
The model owner approves each change, and the model risk team approves material ones.
A material change is validated again before it is used.
Incidents, such as a data feed failure, are reported to the model owner within one business day.
The owner records each incident and its fix in the model inventory.
