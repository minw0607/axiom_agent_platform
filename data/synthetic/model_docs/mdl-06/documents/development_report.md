# Development report: Synthetic Model 06 (call summary assistant)

Model type: generative

## 1. Purpose and intended use
Synthetic Model 06 drafts a summary of each customer service call from its transcript.
The summary is saved to the customer's record for the next agent to read.
It uses a hosted large language model with a fixed prompt.
Customer service agents read the summaries; they must not be used to decide complaints.

## 2. Methodology and design choices
The call transcript and a fixed instruction are sent to a hosted language model.
The instruction asks for the reason for the call, actions taken and open items.
No model was trained; the prompt was refined over 40 example calls.
A hosted model was chosen over training one because no labelled summaries existed.

## 3. Performance testing
The team read 40 summaries and judged them accurate.
Agents in a pilot said the summaries saved time.
No error rate was measured.

## 4. Sensitivity and stress testing
Summaries were produced for very short and very long calls.
Calls longer than 45 minutes were cut to fit the model's input limit.
Summaries of cut calls left out the end of the call.

## 5. Fairness assessment
Summaries were read for calls in English only.
Calls in other languages are transcribed but were not tested.
No comparison across customer groups was made.
