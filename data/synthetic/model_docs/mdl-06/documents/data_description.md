# Data description: Synthetic Model 06 (call summary assistant)

Model type: generative

## 1. Data sources and lineage
Transcripts come from the call recording system's speech-to-text service.
Transcripts are sent to the hosted model and not stored by the provider.
The prompt is versioned in the team's repository.
Transcripts are extracted after each call and matched to the call log daily.
There is no label; agent flags, known within a week, stand in for one.

## 2. Data quality and representativeness
Records with missing values above 5% in any input field were reviewed by hand.
Duplicate records (0.4% of the sample) were removed before training.
The sample covers every region and product the model is used for: calls to the customer service center.

## 3. Personal data and privacy
The personal data the model uses is limited to the call transcript and the customer's record identifier.
The training data contains no names, addresses or account numbers.
Customer identifiers are replaced with random keys before the data leaves the source system.
Access to the modelling data set is limited to the development team and logged.
