# Development report: Synthetic Model 03 (collections prioritization)

Model type: classifier

## 1. Purpose and intended use
Synthetic Model 03 ranks past-due consumer loans for the collections team to contact first.
Customers ranked highest are called within two days; others receive a letter.
The ranking decides the order and channel of contact for each customer.
It must not be used for credit limits, pricing or decisions to sell a debt.

## 2. Limitations and conditions of use
The model covers unsecured consumer loans only.
It was trained during a period of low unemployment and may rank differently in a downturn.
Accounts in hardship programs are excluded and handled by a separate team.

## 3. Methodology and design choices
A logistic regression predicts the chance an account is paid up within 30 days without contact.
Accounts least likely to cure on their own are ranked first.
Inputs are days past due, balance, payment history, contact history and customer age band.
A logistic regression was chosen over boosted trees so that collectors can see why an account ranks high.

## 4. Performance testing
Tested out of time on accounts that went past due in the second half of 2024.
The area under the ROC curve was 0.78 on the test data and 0.80 on the training data.
Calibration by decile was within 2 percentage points of observed cure rates.
The owner's acceptance threshold was an area under the curve of at least 0.75.

## 5. Sensitivity and stress testing
The ranking was recomputed with each input shifted by one standard deviation.
Days past due and payment history had the largest effect on rank.
Ranks were stable: 90% of accounts moved by fewer than 5 places in 100.
A downturn scenario with unemployment 3 points higher was run; the top decile kept 85% of its accounts.

## 6. Human oversight
Synthetic Model 03 output is reviewed by an analyst before any action is taken.
Analysts can override the output and must record a reason for each override.
Overrides are reported monthly to the model owner and reviewed for patterns.
