# Real-time WhatsApp (Meta Cloud API)

This is **not a mock**: messages from a real phone hit Meta’s servers, Meta calls your **webhook**, and Digital MSL sends replies through Meta’s **send message** API.

## Is the webhook / verify token “free”?

| Item | Cost | What it is |
|------|------|------------|
| **Verify token** | Free | A **password you invent** (any random string). Same value in Meta App → Webhook **and** `DIGITAL_MSL_WHATSAPP_VERIFY_TOKEN` in `.env`. Meta does not sell or issue this. |
| **Webhook URL** | Free | Your HTTPS endpoint. Meta POSTs events to it. You pay for hosting/tunnel, not Meta for the webhook itself. |
| **Meta Developer account** | Free | [developers.facebook.com](https://developers.facebook.com) |
| **WhatsApp test in dev** | Free tier | Meta gives a **test business number** and lets you add **up to 5 personal numbers** as recipients for real two-way chat while developing. |
| **Production WhatsApp** | Paid per conversation | After business verification, Meta bills by **conversation** (rates vary by country; India has published tiers). Not needed for first real-time tests on the dev/test number. |

You do **not** need a paid WhatsApp “subscription” just to register a webhook and verify it.

## What you need for real-time testing

1. **Public HTTPS URL** — Meta cannot call `localhost`. Use one of:
   - Deploy backend on a server with TLS, or
   - **ngrok** / Cloudflare Tunnel: `ngrok http 8087` → use `https://xxxx.ngrok-free.app`
2. **Meta app** with **WhatsApp** product added.
3. **Webhook** in Meta App Dashboard → WhatsApp → Configuration:
   - **Callback URL:** `https://<your-host>/api/v1/digital-msl/channels/whatsapp/webhook`
   - **Verify token:** same string as in `.env` (e.g. `DigitalMslVerify2026`)
   - Subscribe to **messages** field.
4. **API credentials** from WhatsApp → API Setup:
   - **Phone number ID** → build send URL  
     `https://graph.facebook.com/v21.0/<PHONE_NUMBER_ID>/messages`
   - **Temporary** or **System user permanent** access token → `DIGITAL_MSL_WHATSAPP_TOKEN`
5. **Add your personal WhatsApp number** under API Setup → “To” / test recipients (required in dev mode).
6. Set **`DIGITAL_MSL_WHATSAPP_WABA_ID`** and restart the backend — it calls Meta `subscribed_apps` so inbound **POST** webhooks are delivered (verify alone only proves GET).
7. Message the **test business number** from your phone — that is real-time WhatsApp.

## `backend/.env`

```env
DIGITAL_MSL_DEFAULT_REPOSITORY_ID=<your Eye_Core_Retina repository id>

DIGITAL_MSL_WHATSAPP_API_URL=https://graph.facebook.com/v21.0/<PHONE_NUMBER_ID>/messages
DIGITAL_MSL_WHATSAPP_TOKEN=<token from Meta API Setup>
DIGITAL_MSL_WHATSAPP_VERIFY_TOKEN=DigitalMslVerify2026
DIGITAL_MSL_WHATSAPP_WABA_ID=<WhatsApp Business Account ID from Meta webhook/API setup>

# Optional: alert Physical MSL phones on escalation (E.164, comma-separated)
DIGITAL_MSL_WHATSAPP_MSL_NOTIFY_PHONES=+91XXXXXXXXXX
DIGITAL_MSL_WHATSAPP_DEMO_GROUP_NAME=Digital MSL Scientific Support
```

Restart the backend after changing `.env`.

## Flow

1. You send WhatsApp **to the business test number** from your registered phone.
2. Meta → `POST .../channels/whatsapp/webhook` → Digital MSL runs the agent and sends the reply.
3. Digital MSL answers or escalates; reply goes out via `WHATSAPP_API_URL`.
4. Check **Admin → delivery log** (`outbox`, channel `whatsapp`) if send fails.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Webhook verify fails | Verify token must match **exactly** in Meta and `.env`; backend must be reachable on HTTPS. |
| No inbound events | Webhook field **messages** + **`DIGITAL_MSL_WHATSAPP_WABA_ID`** (backend subscribes app on startup). ngrok should show **POST**, not only GET verify. |
| Send fails `403` / `#131030` | Recipient not added as test number in Meta dev mode. |
| Duplicate answers | Meta retries if webhook is slow; keep backend reachable and watch logs. |

Admin **Simulate** on Home is optional; ignore it when using the real business number.

## Portal WhatsApp demo (no Meta delivery)

While WABA is restricted, use sidebar **WhatsApp demo** (admin or doctor). It runs the same agent as real WhatsApp and shows replies in a WhatsApp-style chat; outbound Meta send is mocked for that screen only.

Optional: set `DIGITAL_MSL_WHATSAPP_MOCK=true` in `.env` to mock **all** outbound WhatsApp (including real webhook replies) and show **sent** in the delivery log.
