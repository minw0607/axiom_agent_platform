# API error types

Errors use RFC 9457 problem details (`application/problem+json`). The `type` field points to a section of this page and never changes for a given error class.

| Status | Type | Meaning |
|---|---|---|
| 400 | <a id="idempotency-key-required"></a>`idempotency-key-required` | `POST /v1/memos` needs an `Idempotency-Key` header of at most 128 characters. |
| 401 | <a id="unauthorized"></a>`unauthorized` | The `X-API-Key` header is missing or not a valid key. |
| 403 | <a id="forbidden"></a>`forbidden` | The key is valid, but its role cannot perform this action. Only submitter keys can submit. |
| 404 | <a id="not-found"></a>`not-found` | No such job, or the job belongs to another submitter. The two cases look the same on purpose. |
| 405 | <a id="method-not-allowed"></a>`method-not-allowed` | The path does not accept this HTTP method. |
| 413 | <a id="payload-too-large"></a>`payload-too-large` | The request body is over 64 KiB. |
| 422 | <a id="invalid-request"></a>`invalid-request` | The body does not match the schema. The `errors` field lists each problem. |
| 422 | <a id="unknown-package"></a>`unknown-package` | The `package_id` is well formed but not in the synthetic catalog. |
| 422 | <a id="idempotency-key-reused"></a>`idempotency-key-reused` | The key was already used, within 24 hours, with a different request body. |

## Job failure reasons

A failed job returns `status: failed` and a public `error.code`. Internal detail is kept for auditors.

| Code | Meaning |
|---|---|
| `invalid_package` | The package could not be read, or required loan terms are missing. |
| `output_validation_failed` | A model's output failed validation twice, for example a figure that does not match its source line. |
| `model_unavailable` | Every permitted model failed. |
| `no_permitted_model` | The data's sensitivity allows no available model, so the job stopped (fail closed). |
| `document_quarantined` | A borrower document contains instructions aimed at the AI, so the job stopped before any model saw the package. The evidence bundle names the document, line and pattern. |
| `internal_error` | An unexpected error. |
