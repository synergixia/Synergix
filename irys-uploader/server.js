/**
 * irys-uploader — microservicio HTTP que delega la firma de DataItems al SDK
 * oficial @irys/upload-ethereum. El bot Python le manda data+tags por JSON y
 * recibe el txId.
 *
 * Endpoints:
 *   GET  /health   → estado + dirección del wallet
 *   GET  /balance  → balance atómico en el nodo Irys
 *   POST /upload   → { data: <base64>, tags: [{name, value}, ...] } → { id, timestamp }
 */

import express from "express";
import { Uploader } from "@irys/upload";
import { BNB, Ethereum } from "@irys/upload-ethereum";

const PRIVATE_KEY = process.env.PRIVATE_KEY;
const IRYS_TOKEN = (process.env.IRYS_TOKEN || "bnb").toLowerCase();
const IRYS_NODE_URL = process.env.IRYS_NODE_URL || "https://uploader.irys.xyz";
const PORT = parseInt(process.env.PORT || "8083", 10);

if (!PRIVATE_KEY) {
  console.error("❌ PRIVATE_KEY no está configurada");
  process.exit(1);
}

const TOKEN_MAP = {
  bnb: BNB,
  ethereum: Ethereum,
  eth: Ethereum,
};

const tokenCls = TOKEN_MAP[IRYS_TOKEN];
if (!tokenCls) {
  console.error(`❌ IRYS_TOKEN no soportado: ${IRYS_TOKEN} (usa bnb o ethereum)`);
  process.exit(1);
}

let uploader = null;
let initError = null;

async function getUploader() {
  if (uploader) return uploader;
  if (initError) throw initError;
  try {
    uploader = await Uploader(tokenCls).withWallet(PRIVATE_KEY);
    console.log(
      `✅ Irys uploader inicializado — token=${IRYS_TOKEN} address=${uploader.address} node=${IRYS_NODE_URL}`,
    );
    return uploader;
  } catch (err) {
    initError = err;
    console.error("❌ Fallo inicializando Irys uploader:", err.message);
    throw err;
  }
}

const app = express();
app.use(express.json({ limit: "50mb" }));

app.get("/health", async (_req, res) => {
  try {
    const u = await getUploader();
    res.json({ status: "ok", address: u.address, token: IRYS_TOKEN, node: IRYS_NODE_URL });
  } catch (err) {
    res.status(500).json({ status: "error", error: err.message });
  }
});

app.get("/balance", async (_req, res) => {
  try {
    const u = await getUploader();
    const balance = await u.getBalance();
    res.json({
      balance: balance.toString(),
      address: u.address,
      token: IRYS_TOKEN,
    });
  } catch (err) {
    console.error("balance error:", err.message);
    res.status(500).json({ error: err.message });
  }
});

app.post("/upload", async (req, res) => {
  try {
    const { data, tags } = req.body || {};
    if (typeof data !== "string" || data.length === 0) {
      return res.status(400).json({ error: "Campo 'data' (base64) requerido" });
    }
    const cleanTags = Array.isArray(tags)
      ? tags
          .filter((t) => t && typeof t.name === "string" && typeof t.value === "string")
          .map((t) => ({ name: t.name, value: t.value }))
      : [];
    const buffer = Buffer.from(data, "base64");
    const u = await getUploader();
    const receipt = await u.upload(buffer, { tags: cleanTags });
    res.json({
      id: receipt.id,
      timestamp: receipt.timestamp,
      size: buffer.length,
    });
  } catch (err) {
    const msg = err?.message || String(err);
    console.error("upload error:", msg);
    const status = /insufficient/i.test(msg) ? 402 : 500;
    res.status(status).json({ error: msg });
  }
});

app.listen(PORT, "0.0.0.0", async () => {
  console.log(`🚀 irys-uploader escuchando en :${PORT}`);
  try {
    await getUploader();
  } catch {
    // ya se logueó en getUploader
  }
});
