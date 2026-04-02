/**
 * aisynergix/backend/upload.js
 * ═══════════════════════════════════════════════════════════════════════════
 * Motor real de subida a BNB Greenfield Mainnet via SDK oficial.
 *
 * Estructura de rutas: synergix/aisynergix/
 *   aisynergix/SYNERGIXAI/   → cerebro
 *   aisynergix/users/        → perfiles
 *   aisynergix/aportes/      → memoria inmortal
 *   aisynergix/logs/         → auditoría
 *   aisynergix/backups/      → snapshots
 *   aisynergix/discovery/    → tendencias
 *
 * Funciones exportadas:
 *   uploadToGreenfield(content, userId, objectName, metadata)
 *   upsertObject(content, objectName, metadata, onlyTags)
 *   updateObjectTags(objectName, metadata)
 *   objectExists(objectName)
 * ═══════════════════════════════════════════════════════════════════════════
 */

require('dotenv').config({ path: process.env.DOTENV_BACKEND || require('path').join(__dirname, '.env') });
require('dotenv').config({ path: process.env.DOTENV_ROOT    || require('path').join(__dirname, '..', '..', '.env') });

const fs   = require('fs');
const path = require('path');
const os   = require('os');
const {
  Client,
  Long,
  VisibilityType,
  RedundancyType,
  bytesFromBase64,
  GRNToString,
  newObjectGRN,
} = require('@bnb-chain/greenfield-js-sdk');
const { ReedSolomon } = require('@bnb-chain/reed-solomon');
const { ethers }      = require('ethers');

// ── Config Mainnet ────────────────────────────────────────────────────────────
const RPC_URL     = process.env.GF_RPC_URL  || 'https://greenfield-chain.bnbchain.org';
const CHAIN_ID    = process.env.GF_CHAIN_ID || '1017';
const BUCKET_NAME = process.env.GF_BUCKET   || 'synergix';
const client      = Client.create(RPC_URL, CHAIN_ID);

// ── Carpetas públicas dentro de aisynergix/ ───────────────────────────────────
// Solo SYNERGIXAI/ es público (el sitio web lo lee)
// Todo lo demás es privado
const PUBLIC_PREFIXES = ['aisynergix/SYNERGIXAI/'];

function getVisibility(objectName) {
  const isPublic = PUBLIC_PREFIXES.some(p => objectName.startsWith(p));
  return isPublic
    ? VisibilityType.VISIBILITY_TYPE_PUBLIC_READ
    : VisibilityType.VISIBILITY_TYPE_PRIVATE;
}

// ── Helper: wallet ────────────────────────────────────────────────────────────
function getWallet() {
  let pk = process.env.PRIVATE_KEY;
  if (!pk) throw new Error('PRIVATE_KEY no encontrada en .env');
  if (!pk.startsWith('0x')) pk = '0x' + pk;
  return new ethers.Wallet(pk);
}

// ── Helper: broadcast genérico ────────────────────────────────────────────────
async function broadcastTx(tx, wallet) {
  const simulate = await tx.simulate({ denom: 'BNB' });
  const txRes    = await tx.broadcast({
    denom:      'BNB',
    gasLimit:   Number(simulate?.gasLimit),
    gasPrice:   simulate?.gasPrice || '5000000000',
    payer:      wallet.address,
    granter:    '',
    privateKey: wallet.privateKey,
  });
  if (txRes.code !== 0) throw new Error(`Tx failed: ${txRes.rawLog}`);
  return txRes.transactionHash;
}

// ── Helper: metadata dict → tags array (max 4 tags Greenfield) ───────────────
function metaToTags(metadata) {
  if (!metadata || typeof metadata !== 'object') return [];
  const entries = Object.entries(metadata).slice(0, 4);
  return entries.map(([k, v]) => ({
    key:   k.replace('x-amz-meta-', '').slice(0, 32),
    value: String(v).slice(0, 256),
  }));
}

// ── Helper: contenido mínimo (Greenfield rechaza objetos vacíos) ──────────────
function ensureMinContent(content, objectName) {
  if (!content || content.trim().length < 32) {
    return `# Synergix | ${objectName} | ${new Date().toISOString()}\n${content || ''}`;
  }
  return content;
}

// ── Helper: build checksums para Reed-Solomon ─────────────────────────────────
async function buildChecksums(tmpPath) {
  const fileBuffer = fs.readFileSync(tmpPath);
  const rs         = new ReedSolomon();
  const hashRes    = await rs.encode(Uint8Array.from(fileBuffer));
  const checksums  = hashRes.map(x => bytesFromBase64(x));
  return { fileBuffer, checksums };
}

// ═════════════════════════════════════════════════════════════════════════════
// objectExists — verifica si un objeto existe via headObject
// ═════════════════════════════════════════════════════════════════════════════
async function objectExists(objectName) {
  try {
    const res = await client.object.headObject(BUCKET_NAME, objectName);
    return !!(res && res.objectInfo);
  } catch (e) {
    return false;
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// uploadToGreenfield — crea objeto nuevo (falla si ya existe)
// ═════════════════════════════════════════════════════════════════════════════
async function uploadToGreenfield(content, userId, objectName, metadata) {
  // Auto-generar nombre si no se provee
  if (!objectName) {
    const now   = new Date();
    const month = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
    const ts    = Date.now();
    objectName  = `aisynergix/aportes/${month}/${userId}_${ts}.txt`;
  }

  content = ensureMinContent(content, objectName);
  const tmpPath = path.join(os.tmpdir(), `gf_${Date.now()}.txt`);

  try {
    fs.writeFileSync(tmpPath, content, 'utf8');
    const wallet = getWallet();
    const { fileBuffer, checksums } = await buildChecksums(tmpPath);
    const sps       = await client.sp.getStorageProviders();
    const primarySP = sps[0];
    const tags      = metaToTags(metadata);

    // Visibilidad automática según la ruta
    const visibility = getVisibility(objectName);

    const createTx = await client.object.createObject({
      bucketName:       BUCKET_NAME,
      objectName,
      creator:          wallet.address,
      visibility,
      contentType:      'text/plain',
      redundancyType:   RedundancyType.REDUNDANCY_EC_TYPE,
      payloadSize:      Long.fromNumber(fileBuffer.length),
      expectChecksums:  checksums,
      expectCheckSums:  checksums,
      primarySpAddress: primarySP.operatorAddress,
      tags: tags.length > 0 ? { tags } : undefined,
    });

    const txHash = await broadcastTx(createTx, wallet);

    await client.object.uploadObject(
      {
        bucketName:  BUCKET_NAME,
        objectName,
        body: {
          name:    path.basename(objectName),
          type:    'text/plain',
          size:    fileBuffer.length,
          content: fileBuffer,
        },
        txnHash: txHash,
      },
      { type: 'ECDSA', privateKey: wallet.privateKey }
    );

    if (fs.existsSync(tmpPath)) fs.unlinkSync(tmpPath);
    console.log(`✅ Creado: ${objectName} | tx: ${txHash}`);
    return { success: true, cid: txHash, objectName, bucket: BUCKET_NAME };

  } catch (err) {
    if (fs.existsSync(tmpPath)) try { fs.unlinkSync(tmpPath); } catch(_) {}
    throw err;
  }
}

// ═════════════════════════════════════════════════════════════════════════════
// updateObjectTags — actualiza solo los tags on-chain (MsgSetTag)
// Más barato que recrear el objeto. Para usuarios que cambian puntos frecuente.
// ═════════════════════════════════════════════════════════════════════════════
async function updateObjectTags(objectName, metadata) {
  const wallet   = getWallet();
  const tags     = metaToTags(metadata);
  if (tags.length === 0) return { success: true, updated: false };

  const resource = GRNToString(newObjectGRN(BUCKET_NAME, objectName));
  const setTagTx = await client.storage.setTag({
    operator: wallet.address,
    resource,
    tags: { tags },
  });

  const txHash = await broadcastTx(setTagTx, wallet);
  console.log(`✅ Tags actualizados: ${objectName} | tx: ${txHash}`);
  return { success: true, cid: txHash, objectName, updated: true };
}

// ═════════════════════════════════════════════════════════════════════════════
// deleteAndRecreate — borra y recrea con nuevo contenido
// ═════════════════════════════════════════════════════════════════════════════
async function deleteAndRecreate(content, objectName, metadata) {
  const wallet = getWallet();
  const deleteTx = await client.object.deleteObject({
    bucketName: BUCKET_NAME,
    objectName,
    operator:   wallet.address,
  });
  await broadcastTx(deleteTx, wallet);
  console.log(`🗑️  Eliminado: ${objectName}`);
  await new Promise(r => setTimeout(r, 3000));
  return await uploadToGreenfield(content, 'system', objectName, metadata);
}

// ═════════════════════════════════════════════════════════════════════════════
// upsertObject — CREATE o UPDATE automático
//   onlyTags=true  → si existe, solo actualiza tags (barato)
//   onlyTags=false → si existe, delete + recreate
// ═════════════════════════════════════════════════════════════════════════════
async function upsertObject(content, objectName, metadata, onlyTags = false) {
  const exists = await objectExists(objectName);

  if (!exists) {
    console.log(`📝 Creando nuevo objeto: ${objectName}`);
    return await uploadToGreenfield(content, 'system', objectName, metadata);
  }

  if (onlyTags) {
    console.log(`🏷️  Solo tags: ${objectName}`);
    return await updateObjectTags(objectName, metadata);
  } else {
    console.log(`🔄 Delete+Recreate: ${objectName}`);
    return await deleteAndRecreate(content, objectName, metadata);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
module.exports = {
  uploadToGreenfield,
  updateObjectTags,
  upsertObject,
  objectExists,
};

// ── Test directo ──────────────────────────────────────────────────────────────
if (require.main === module) {
  const mode = process.argv[2] || 'test';
  const obj  = process.argv[3] || null;

  if (mode === 'test') {
    uploadToGreenfield(
      'Test Synergix aisynergix/ ' + new Date().toISOString(),
      'test_user',
      null,
      { 'x-amz-meta-type': 'test' }
    )
      .then(r  => { console.log('__RESULT__:' + JSON.stringify(r)); process.exit(0); })
      .catch(e => { console.error('__ERROR__:' + e.message);        process.exit(1); });

  } else if (mode === 'exists' && obj) {
    objectExists(obj)
      .then(r  => { console.log('exists:', r); process.exit(0); })
      .catch(e => { console.error(e.message);  process.exit(1); });
  }
}
