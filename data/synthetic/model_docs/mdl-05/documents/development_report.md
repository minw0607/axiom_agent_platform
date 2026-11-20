# Development report: Synthetic Model 05 (savings offer propensity)

Model type: classifier

## 1. Purpose and intended use
Synthetic Model 05 predicts which customers are likely to open a savings account.
Customers above the cut-off receive a savings offer by email.
The offer is the same for everyone who receives it.
The marketing team uses the output; it must not be used for credit or pricing decisions.

## 2. Limitations and conditions of use
The model was trained on customers who had a current account for at least a year.
Newer customers are not scored.
It predicts interest in the offer, not whether the product suits the customer.
It was tested only on customers from the training period, not on a later period.

## 3. Methodology and design choices
A random forest with 60 inputs on account activity and past product holdings.
The cut-off was set so that 10% of eligible customers receive the offer.
Inputs were chosen from 300 candidates by their importance on a validation sample.
A random forest was chosen over a logistic regression because it ranked responders better on the validation sample.

## 4. Performance testing
Tested on a holdout of 20% of customers, drawn at random from the same period.
Customers in the top decile opened accounts at 3.2 times the average rate.
The marketing team's benchmark was at least 2.5 times the average rate.
The model was not tested on a later period.

## 5. Sensitivity and stress testing
Response rates were compared for customers with high and low balances.
The top decile's response rate fell to 2.1 times average among low-balance customers.
No stress scenario was run, as the model drives marketing offers only.

## 6. Fairness assessment
Offer rates were compared across age bands and regions.
Customers over 70 received offers at half the average rate.
The owner judged this acceptable because the offer is optional.

## 7. Human oversight
Synthetic Model 05 output is reviewed by an analyst before any action is taken.
Analysts can override the output and must record a reason for each override.
Overrides are reported monthly to the model owner and reviewed for patterns.
