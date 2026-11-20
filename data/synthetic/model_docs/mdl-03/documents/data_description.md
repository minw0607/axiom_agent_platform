# Data description: Synthetic Model 03 (collections prioritization)

Model type: classifier

## 1. Data sources and lineage
Account data comes from the loan servicing system, extracted daily.
Contact history comes from the collections dialer log.
Both sources are matched on the account number, then the number is replaced by a key.
Each daily extract is reconciled to the servicing system's count of past-due accounts.
The cure label is known 30 days after an account goes past due, so the last 30 days are excluded.

## 2. Data quality and representativeness
Records with missing values above 5% in any input field were reviewed by hand.
Duplicate records (0.4% of the sample) were removed before training.
The sample covers every region and product the model is used for: consumer loans past due.

## 3. Personal data and privacy
The personal data the model uses is limited to each borrower's loan balance, payment and contact history, and age band.
The training data contains no names, addresses or account numbers.
Customer identifiers are replaced with random keys before the data leaves the source system.
Access to the modelling data set is limited to the development team and logged.
