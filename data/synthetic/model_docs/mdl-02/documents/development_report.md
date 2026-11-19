# Development report: Synthetic Model 02 (card fraud alerting)

Model type: classifier

## 1. Purpose and intended use
Synthetic Model 02 scores card transactions for fraud risk in real time.
Transactions above the alert threshold are queued for a fraud analyst.
The score does not block a transaction on its own.

## 2. Limitations and conditions of use
The model was trained on card-present and online transactions only.
Wallet payments, introduced after the training period, are scored as online transactions.
Fraud patterns change quickly, so the model is expected to degrade without retraining.

## 3. Methodology and design choices
A gradient boosted tree model with 140 input features.
Features describe the transaction, the card's recent history and the merchant.
Hyperparameters were chosen by cross-validation on the 2023 data.

## 4. Performance testing
Tested out of time on six months of 2024 transactions.
At the alert threshold, the model catches 71% of fraud value with 2.5 alerts per fraud case.
The previous rules-based system caught 58% at the same alert volume.

## 5. Sensitivity and stress testing
Performance was measured separately for each channel and for transaction size bands.
Detection in the smallest size band was 49%, the lowest of any band.
Removing each of the ten strongest features in turn lowered detection by at most 6 points.

## 6. Fairness assessment
Alert rates were compared across age bands and regions on the test data.
Alert rates differed by at most 0.3 percentage points between groups.
The model uses no protected characteristic as an input.

## 7. Human oversight
Synthetic Model 02 output is reviewed by an analyst before any action is taken.
Analysts can override the output and must record a reason for each override.
Overrides are reported monthly to the model owner and reviewed for patterns.
