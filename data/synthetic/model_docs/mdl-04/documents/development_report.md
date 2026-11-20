# Development report: Synthetic Model 04 (mortgage prepayment)

Model type: forecast

## 1. Purpose and intended use
Synthetic Model 04 forecasts prepayment speeds of fixed-rate mortgages for hedging.
The forecast is used by the treasury hedging desk to set the size of the interest rate hedge each quarter.
It is not used for decisions about individual borrowers.

## 2. Limitations and conditions of use
The model covers fixed-rate loans with terms of 15 to 30 years.
Refinancing incentives beyond 300 basis points were not observed in the data.
Loans from a portfolio acquired in 2023 are assumed to behave like originated loans.
Forecasts for rate moves beyond 300 basis points should not be relied on.

## 3. Methodology and design choices
A multinomial logit model of monthly prepayment, by loan age and rate incentive.
Seasonality is captured by a month-of-year factor.
Burnout is modelled by the number of past months with a refinancing incentive.
A logit model was chosen over a survival model because it separates refinancing from other prepayment.

## 4. Performance testing
The model fits the 2015 to 2024 history closely.
The fitted prepayment rate is within 0.2% of the actual rate in every year of that history.
R-squared of the fitted monthly rates is 0.94.

## 5. Sensitivity and stress testing
To be completed in the next release.

## 6. Fairness assessment
The forecast is for the portfolio as a whole and makes no decision about a borrower.
No group of borrowers is scored or treated differently because of it.
The owner will reassess this if the model is used at the loan level.

## 7. Human oversight
Synthetic Model 04 output is reviewed by an analyst before any action is taken.
Analysts can override the output and must record a reason for each override.
Overrides are reported monthly to the model owner and reviewed for patterns.
