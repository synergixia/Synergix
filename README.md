# Synergix — The Sovereign Ghost Node AI 🧠🔗

Synergix is a revolutionary Telegram bot architecture designed for **Stateless Operation** and **Zero-Knowledge Privacy**. It treats a decentralized bucket (BNB Greenfield / DCellar) as its primary database, ensuring that the local server (Hetzner 8GB/4-Core) remains a "Ghost Node" with no persistent local state.

## 🚀 Key Innovation: The Ghost Protocol
Synergix implements a **Ghost Identity** system. User Telegram UIDs are never stored on-chain. Every UID is processed through an irreversible cryptographic hash (SHA-256 + Salt) before touching the Web3 layer. This ensures that user data is immortal and decentralized, but the real identity behind the points and contributions remains a cryptographic secret.

## 🛠 Features
- **Decentralized State:** Uses Greenfield Object Metadata (Tags) to manage points, ranks, and FSM states on 0-byte ghost files.
- **Local LLM Dual-Engine:** - **Judge (Qwen 0.5B):** Real-time technical quality validation.
  - **Thinker (Qwen 1.5B):** Multilingual sovereign Oracle with RAG support.
- **Immortal Memory:** User contributions are evaluated, compiled into a FAISS vector index, and stored on-chain.
- **Stateless Resilience:** If the server is wiped, the node reconstructs itself entirely from the bucket.

## 🏗 Setup
1. Clone the repository.
2. Fill the `.env` file with your `TELEGRAM_TOKEN`, `PRIVATE_KEY` (ECDSA), and `SALT`.
3. Deploy models: `cd docker && docker-compose up -d`.
4. Run: `python -m aisynergix.bot.bot`.
