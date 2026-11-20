# Monitoring plan: Synthetic Model 01 (deposit run-off forecast)

Model type: forecast

## 1. Monitoring metrics and thresholds
Forecast error is measured each month against actual balances.
An error above 3.0% for two consecutive months triggers a review by the owner.
An error above 5.0% in any month is escalated to the model risk committee.
Segment shares are compared with the training data each quarter for drift.

## 2. Change management and incidents
Any change to inputs, method or thresholds is logged and reviewed before release.
The model owner approves each change, and the model risk team approves material ones.
A material change is validated again before it is used.
Incidents, such as a data feed failure, are reported to the model owner within one business day.
The owner records each incident and its fix in the model inventory.
