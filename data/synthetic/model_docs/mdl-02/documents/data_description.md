# Data description: Synthetic Model 02 (card fraud alerting)

Model type: classifier

## 1. Data sources and lineage
Transactions come from the card authorization system, extracted daily.
Each daily extract is reconciled to the authorization count before use.
Fraud labels come from confirmed chargebacks, which arrive up to 90 days later.
Labels for the last 90 days of the sample were therefore excluded.

## 2. Data quality and representativeness
Records with missing values above 5% in any input field were reviewed by hand.
Duplicate records (0.4% of the sample) were removed before training.
The sample covers every region and product the model is used for: card transactions in all channels.

## 3. Personal data and privacy
The personal data the model uses is limited to each cardholder's transaction history and card details.
The training data contains no names, addresses or account numbers.
Customer identifiers are replaced with random keys before the data leaves the source system.
Access to the modelling data set is limited to the development team and logged.
