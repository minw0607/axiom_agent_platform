# Development report: Synthetic Model 01 (deposit run-off forecast)

Model type: forecast

## 1. Purpose and intended use
Synthetic Model 01 forecasts monthly run-off of retail deposit balances for liquidity planning.
It is used by the treasury team to size the liquidity buffer each month.
It is not used for pricing or for decisions about individual customers.

## 2. Limitations and conditions of use
The model assumes rate changes of at most 200 basis points within a year.
Forecasts beyond 12 months are outside its tested range and are not used.
Deposit products launched after the training period are mapped to the nearest existing product.

## 3. Methodology and design choices
A segmented survival model estimates the share of balances remaining each month.
Segments are product, customer tenure band and balance band (24 segments).
A survival model was chosen over a regression because balances leave gradually.
The rate sensitivity is a single coefficient per segment, estimated on 2015 to 2024 data.

## 4. Performance testing
The model was tested out of time on 2024 data that was not used to fit it.
Mean absolute error of the 12-month forecast was 2.1% of balances.
The forecast stayed within the 3.0% tolerance set by treasury in 11 of 12 months.
Results were compared with the previous model, whose error was 3.4%.

## 5. Sensitivity and stress testing
Forecasts were recomputed with rates 100 and 200 basis points higher and lower.
Run-off rose by at most 4.5% of balances under the largest rise.
A stress scenario with a one-month outflow of 10% was run, and the buffer covered it.

## 6. Fairness assessment
The model forecasts total balances, not outcomes for individual customers.
No decision about a customer uses its output, so no group of customers is scored.
The owner will reassess this if the output is ever used for individual offers.

## 7. Human oversight
Synthetic Model 01 output is reviewed by an analyst before any action is taken.
Analysts can override the output and must record a reason for each override.
Overrides are reported monthly to the model owner and reviewed for patterns.
