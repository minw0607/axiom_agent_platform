# Monitoring plan: Synthetic Model 06 (call summary assistant)

Model type: generative

## 1. Monitoring metrics and thresholds
Agents can flag a summary as wrong with one click.
The flag rate is reported weekly.
A flag rate above 5% in any week triggers a review of the prompt.

## 2. Change management and incidents
Any change to inputs, method or thresholds is logged and reviewed before release.
The model owner approves each change, and the model risk team approves material ones.
A material change is validated again before it is used.
Incidents, such as a data feed failure, are reported to the model owner within one business day.
The owner records each incident and its fix in the model inventory.
