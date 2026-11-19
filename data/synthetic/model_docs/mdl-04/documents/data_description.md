# Data description: Synthetic Model 04 (mortgage prepayment)

Model type: forecast

## 1. Data sources and lineage
Loan data comes from the mortgage servicing system, extracted monthly.
Market rates come from the published rate history used across the bank.
Acquired loans come from the seller's tape, converted to the same fields.

## 2. Data quality and representativeness
Records with missing values above 5% in any input field were reviewed by hand.
Duplicate records (0.4% of the sample) were removed before training.
The sample covers every region and product the model is used for: fixed-rate residential mortgages.

## 3. Personal data and privacy
The training data contains no names, addresses or account numbers.
Customer identifiers are replaced with random keys before the data leaves the source system.
Access to the modelling data set is limited to the development team and logged.
