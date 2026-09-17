# Configurable connector contract (version 1)

The workspace does not assume a vendor. A gateway is a small server-side adapter
that translates this contract into the APIs of your chosen email, WhatsApp, SMS,
payment or accounting provider. **A provider's ordinary API URL is not sufficient**:
it must understand the contract below. Vendor adapters and live credentials are
not included or selected in this release. The application integration paths are
tested with HTTP transport simulations.

## Configure a gateway

1. Implement and host the adapter at an HTTPS endpoint on port 443.
2. Add its hostname to `CONNECTOR_ALLOWED_HOSTS` on the application server.
3. In **Team & integrations**, select a connector kind, enter its endpoint and
   separate API and webhook secrets (at least 16 characters each), and enable it.
4. Validate a sandbox operation. Live message submission additionally requires
   `WORKSPACE_LIVE_ENABLED=true` and a reviewed reminder or authorized policy.

Gateway hosts must be controlled and trusted by the server operator. Do not add
untrusted or internal infrastructure hosts to the allowlist. Redirects and
environment proxy inheritance are disabled. Requests time out after 20 seconds;
responses are limited to 5 MB. Do not log authorization headers or customer data.

Saved credentials use authenticated Fernet encryption. The encryption key comes
from `WORKSPACE_MASTER_KEY` or the local `.data/connector.key`. Protect that key
using the host account's access controls; possession of both database and key
permits decryption. See the [cryptography Fernet documentation](https://cryptography.io/en/latest/fernet/).

## Requests

Every operation is a JSON POST to the configured endpoint:

```json
{
  "version": 1,
  "operation": "message.send",
  "business_id": "the-workspace-business-id",
  "request_id": "stable-operation-id",
  "payload": {
    "recipient": "+919876543210",
    "subject": "Invoice reminder",
    "body": "The reviewed message",
    "language": "en",
    "template_id": null
  }
}
```

Headers:

- `Authorization: Bearer <API token>`
- `Idempotency-Key: <request_id>`
- `X-Reminder-Timestamp: <UTC Unix seconds>`
- `X-Reminder-Signature: <lowercase HMAC-SHA256 hex>`

The signature input is the timestamp, a literal dot, then the exact raw JSON body
bytes. The HMAC key is the API token. Verify the bearer token and signature, reject
stale requests, bind each token to its configured business, and enforce provider
quotas. The gateway **must** persist each idempotency key before performing a
side effect and return the original outcome on duplicate requests. An HTTP
timeout must never cause the gateway to create a second message or payment link.

Responses must be HTTP 200 JSON and echo `request_id`. A definite pre-submission
rejection can return a 4xx code (except 408, 409, 429). All other non-200 responses,
timeouts, invalid response bodies, and unmatched IDs are treated as uncertain;
the app does not retry the side effect automatically. **Never return a definite
rejection after a provider may have accepted a request.**

### Message submission: `message.send`

Available for `email`, `sms`, and `whatsapp`. Payload contains the recipient,
subject, body, language, and application template ID shown above.

```json
{"request_id":"stable-operation-id","status":"submitted","provider_id":"provider-message-id"}
```

The adapter sets the business's sender identity. For WhatsApp, map the application
template to an approved provider template, validate parameters and contact
permission, and reject unsupported content. Do not silently substitute text
that changes the approved payment facts. SMS adapters must check message limits
and encoding. Submission does not prove delivery; report that via webhooks.

### Payment links: `payment_link.create`

```json
{"invoice_no":"INV-1","amount_minor":1050000,"currency":"INR","customer_email":"customer@example.com"}
```

Return the created HTTPS link, provider ID and the exact requested monetary value:

```json
{"request_id":"stable-operation-id","provider_id":"link_123","url":"https://your-payment-provider.example/pay/123","amount_minor":1050000,"currency":"INR"}
```

Amounts are integer minor units; this application's ledger supports two-decimal
currencies. Provider IDs must be stable and unique within the business. Do not
notify the customer from this operation: the operator reviews how the link is
shared. Link creation is an explicit user action; it does not depend on the live
message switch. To retire a link, cancel it in the payment provider and reconcile
it in the app. Previously issued links must be retired before replacement if
their amount no longer matches the balance.

### Payment-link reconciliation: `payment_link.lookup`

Request payload: `{"original_request_id":"original-create-request-id"}`.
This is a **read-only** lookup. Return the create response fields (with the new
lookup request ID), or:

```json
{"request_id":"lookup-request-id","state":"cancelled"}
```

`state: "not_created"` is allowed only if the adapter can prove no link was
created and no original operation is still running. The app retains uncertain
operations until an explicit reconciliation.

### Accounting import: `invoices.list`

Request payload: `{}`. The gateway performs provider authentication and paging,
then returns up to 5,000 normalized invoice records as a complete review batch:

```json
{"request_id":"operation-id","complete":true,"invoices":[
  {"invoice_no":"INV-1","client_name":"Customer","email":"customer@example.com","amount":"1000.00","amount_paid":"0.00","due_date":"2026-09-01","status":"unpaid","currency":"INR"}
]}
```

The operator reviews and imports these records. Import is atomic. Existing
invoice settlements must match the application ledger; differences need reviewed
receipts/reversals rather than overwriting local payment history. Missing invoices
are retained. This release does not push accounting journal entries or reconcile
arbitrary bank descriptions automatically.

## Signed events

POST to `/webhooks/<business_id>/<kind>` on the webhook service. Use the same
timestamp and signature headers, signing with the **webhook secret**, not the API
token. The allowed clock difference is 300 seconds. Bodies are limited to 512 KB.
Send a new timestamp/signature on each retry but retain the original event ID.

### Confirmed payment

```json
{"event_id":"event_1","type":"payment.received","link_id":"link_123","payment_id":"payment_123","amount_minor":50000,"currency":"INR"}
```

Only emit after verifying the provider event and confirming the payment is
captured/settled according to the provider's API. The app requires an existing
link in the same business, matching currency, and a positive amount that does
not exceed the link or invoice balance. Partial receipts are supported. Stable
payment IDs prevent the same payment being applied under multiple event IDs.
Rejected or mismatched events require provider-side review; they do not change
the ledger. Refunds/chargebacks require an audited receipt reversal in this release.

### Delivery

```json
{"event_id":"event_2","type":"message.status","message_id":"provider-message-id","status":"delivered"}
```

Status is `delivered`, `read`, or `bounced`. IDs must match the saved submission.
Out-of-order events cannot reduce `read` to `delivered` or `bounced`. If a callback
arrives before the submission response is recorded, retry it after a delay.

### Contact opt-out

```json
{"event_id":"event_3","type":"contact.opted_out","recipient":"+919876543210"}
```

Route under the relevant email/SMS/WhatsApp kind. It removes that channel's
permission from all matching contacts in the business. Customer-portal opt-out
pauses all channels. Already submitted messages cannot be recalled.

Successful results are `{"result":"processed"}` or `{"result":"duplicate"}`.
Invalid requests receive 400; oversized requests receive 413. Keep an adapter-side
event log and failed-event queue, distinguish a permanent mismatch from a temporary
message-recording race, and provide an operator reconciliation workflow. Do not
interpret a 400 as a successfully recorded receipt.
