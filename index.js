require("dotenv").config();
const express = require("express");
const axios = require("axios");
const Anthropic = require("@anthropic-ai/sdk");

const app = express();
app.use(express.json());

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });

// ── Trello ────────────────────────────────────────────────────────────────────

const TRELLO_LIST_MAP = {
  Preventivi: process.env.TRELLO_LIST_PREVENTIVI,
  Appuntamenti: process.env.TRELLO_LIST_APPUNTAMENTI,
  Varie: process.env.TRELLO_LIST_VARIE,
};

async function createTrelloCard({ listId, title, description }) {
  const url = "https://api.trello.com/1/cards";
  const params = {
    key: process.env.TRELLO_API_KEY,
    token: process.env.TRELLO_TOKEN,
    idList: listId,
    name: title,
    desc: description,
  };
  const { data } = await axios.post(url, null, { params });
  return data;
}

// ── AI Classification ─────────────────────────────────────────────────────────

const SYSTEM_PROMPT = `Sei un assistente specializzato nella gestione clienti di un'officina gommista chiamata "Bricherasio Gomme".
Ricevi messaggi WhatsApp dai clienti e devi classificarli ed estrarre le informazioni utili.

Classifica ogni messaggio in UNA delle seguenti categorie:
- "Preventivi": richieste di prezzi, offerte, costi per pneumatici, cerchi, montaggio, ecc.
- "Appuntamenti": richieste di prenotazione, cambio gomme, conferme orari, disdette.
- "Varie": tutto il resto (lamentele, domande generali, ringraziamenti, ecc.)

Restituisci ESCLUSIVAMENTE un oggetto JSON valido con questa struttura (nessun testo prima o dopo):
{
  "categoria": "<Preventivi|Appuntamenti|Varie>",
  "urgenza": "<alta|media|bassa>",
  "riepilogo": "<breve riepilogo in italiano del messaggio in max 2 righe>",
  "dati_tecnici": {
    "misura_pneumatico": "<es. 205/55 R16 o null>",
    "modello_auto": "<es. Fiat Panda 2020 o null>",
    "data_richiesta": "<es. lunedì mattina o null>",
    "note": "<qualsiasi altro dato tecnico rilevante o null>"
  }
}`;

async function classifyMessage(text) {
  const message = await anthropic.messages.create({
    model: "claude-sonnet-4-6",
    max_tokens: 512,
    system: SYSTEM_PROMPT,
    messages: [{ role: "user", content: text }],
  });

  let raw = message.content[0].text.trim();
  // Rimuove eventuali blocchi markdown ```json ... ```
  raw = raw.replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/, "").trim();
  return JSON.parse(raw);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function buildCardDescription(originalText, aiResult) {
  const d = aiResult.dati_tecnici;
  const lines = [
    `📱 *Messaggio originale:*`,
    originalText,
    ``,
    `🤖 *Riepilogo AI:*`,
    aiResult.riepilogo,
    ``,
    `📋 *Dati estratti:*`,
    `• Urgenza: ${aiResult.urgenza}`,
    `• Misura pneumatico: ${d.misura_pneumatico ?? "—"}`,
    `• Modello auto: ${d.modello_auto ?? "—"}`,
    `• Data/orario richiesto: ${d.data_richiesta ?? "—"}`,
    `• Note tecniche: ${d.note ?? "—"}`,
  ];
  return lines.join("\n");
}

function extractMessageData(body) {
  try {
    const entry = body.entry?.[0];
    const change = entry?.changes?.[0];
    const value = change?.value;
    const msg = value?.messages?.[0];

    if (!msg) return null;

    const phone = msg.from;
    const text = msg.text?.body ?? null;
    const contactName =
      value?.contacts?.[0]?.profile?.name ?? null;

    return { phone, text, contactName };
  } catch {
    return null;
  }
}

// ── Webhook ───────────────────────────────────────────────────────────────────

// Verifica del webhook (richiesta GET di Meta)
app.get("/webhook", (req, res) => {
  const mode = req.query["hub.mode"];
  const token = req.query["hub.verify_token"];
  const challenge = req.query["hub.challenge"];

  if (mode === "subscribe" && token === process.env.META_VERIFY_TOKEN) {
    console.log("✅ Webhook verificato da Meta.");
    return res.status(200).send(challenge);
  }
  res.sendStatus(403);
});

// Ricezione messaggi (POST)
app.post("/webhook", async (req, res) => {
  // Risponde subito 200 a Meta per evitare retry
  res.sendStatus(200);

  try {
    const data = extractMessageData(req.body);

    if (!data || !data.text) {
      console.log("⚠️  Messaggio non testuale o struttura non riconosciuta, ignorato.");
      return;
    }

    const { phone, text, contactName } = data;
    const cardTitle = contactName ? `${contactName} (${phone})` : phone;

    console.log(`📨 Messaggio da ${cardTitle}: "${text}"`);

    // Classificazione AI
    let aiResult;
    try {
      aiResult = await classifyMessage(text);
    } catch (err) {
      console.error("❌ Errore classificazione AI:", err.message);
      // Fallback: categoria Varie
      aiResult = {
        categoria: "Varie",
        urgenza: "bassa",
        riepilogo: "Classificazione AI non disponibile.",
        dati_tecnici: {
          misura_pneumatico: null,
          modello_auto: null,
          data_richiesta: null,
          note: null,
        },
      };
    }

    console.log(`🏷️  Categoria: ${aiResult.categoria} | Urgenza: ${aiResult.urgenza}`);

    // Selezione lista Trello
    const listId = TRELLO_LIST_MAP[aiResult.categoria] ?? TRELLO_LIST_MAP["Varie"];
    if (!listId) {
      console.error("❌ ID lista Trello mancante nel file .env per categoria:", aiResult.categoria);
      return;
    }

    // Creazione card Trello
    const description = buildCardDescription(text, aiResult);
    const card = await createTrelloCard({ listId, title: cardTitle, description });

    console.log(`✅ Card Trello creata: "${card.name}" → ${card.shortUrl}`);
  } catch (err) {
    console.error("❌ Errore nel processamento del messaggio:", err.message);
  }
});

// ── Healthcheck ───────────────────────────────────────────────────────────────

app.get("/health", (_req, res) => res.json({ status: "ok", service: "bricherasio-gomme-bot" }));

// ── Avvio ─────────────────────────────────────────────────────────────────────

const PORT = process.env.PORT ?? 3000;
app.listen(PORT, () => console.log(`🚀 Server avviato sulla porta ${PORT}`));
