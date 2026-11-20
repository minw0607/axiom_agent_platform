# Data description: Synthetic Model 01 (deposit run-off forecast)

Model type: forecast

## 1. Data sources and lineage
Monthly balances come from the deposit ledger, extracted on the first business day.
Rates come from the published rate history maintained by treasury.
Each extract is reconciled to the general ledger total before use.
There is no label delay: actual balances are known at each month end.

## 2. Data quality and representativeness
Records with missing values above 5% in any input field were reviewed by hand.
After that review, every input field was at least 98% complete.
Duplicate records (0.4% of the sample) were removed before training.
The sample covers every region and product the model is used for: all retail deposit products.

## 3. Personal data and privacy
The personal data the model uses is limited to each customer's deposit balances, product and tenure.
The training data contains no names, addresses or account numbers.
Customer identifiers are replaced with random keys before the data leaves the source system.
Access to the modelling data set is limited to the development team and logged.
