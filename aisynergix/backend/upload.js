/**
 * aisynergix/backend/upload.js
 * ══════════════════════════════════════════════════════════════════════════════
 * Motor de subida a BNB Greenfield Mainnet via SDK oficial.
 *
 * Bucket: synergixai
 * Raíz:   aisynergix/
 *
 * Estructura gestionada:
 *   aisynergix/SYNERGIXAI/   → cerebro fusionado (público)
 *   aisynergix/users/        → perfiles de usuario (privado)
 *   aisynergix/aportes/      → memoria inmortal (privado)
 *   aisynergix/logs/         → auditoría (privado)
 *   aisynergix/backups/      → snapshots de seguridad (privado)
 *   aisynergix/data/         → DB versionada (privado)
 *   aisynergix/discovery/    → tendencias para el challenge (privado)
 *
 * Funciones exportadas:
 *   uploadToGreenfield(content, userId, objectName, metadata)
 *   upsertObject(content, objectName, metadata, onlyTags)
 *   updateObjectTags(objectName, metadata)
 *   objectExists(objectName)
 * ══════════════════════════════════════════════════════════════════════════════
 */

// Cargar .env desde ROOT_DIR (/root/Synergix/) y BASE_DIR (/root/Synergix/aisynergix/)
require('dotenv').config({
  path: process.env.DOTENV_ROOT || require('path').join(__dirname, '..', '..', '.env')
});
require('dotenv').config({
  path: process.env.DOTENV_BACKEND || require('path').join(__dirname, '.env')
});

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

// ── Config ─────────────────────────────────────────────────────────────────────
const GF_RPC_URL  = process.env.GF_RPC_URL  || 'https://greenfield-chain.bnbchain.org';
const GF_CHAIN_ID = process.env.GF_CHAIN_ID || '1017';
const BUCKET_NAME = process.env.GF_BUCKET   || 'synergixai';
const client      = Client.create(GF_RPC_URL, GF_CHAIN_ID);

// Carpetas públicas (el cerebro es legible por el sitio web)
const PUBLIC_PREFIXES = ['aisynergix/SYNERGIXAI/'];

function getVisibility(objectName) {
  return PUBLIC_PREFIXES.some(p => objectName.startsWith(p))
    ? VisibilityType.VISIBILITY_TYPE_PUBLIC_READ
    : VisibilityType.VISIBILITY_TYPE_PRIVATE;
}

// ── Wallet ──────────────────────────────────────────────────────────────────────
function getWallet() {
  let pk = process.env.PRIVATE_KEY;
  if (!pk) throw new Error('PRIVATE_KEY no encontrada en .env');
  if (!pk.startsWith('0x')) pk = '0x' + pk;
  return new ethers.Wallet(pk);
}

// ── Broadcast genérico ──────────────────────────────────────────────────────────
async function broadcastTx(tx, wallet) {
  const simulate = await tx.simulate({ denom: 'BNB' });
  const result   = await tx.broadcast({
    denom:      'BNB',
    gasLimit:   Number(simulate?.gasLimit || 210000),
    gasPrice:   simulate?.gasPrice || '5000000000',
    payer:      wallet.address,
    granter:    '',
    privateKey: wallet.privateKey,
  });
  if (result.code !== 0) {
    throw new Error(`Tx failed (code ${result.code}): ${result.rawLog}`);
  }
  return result.transactionHash;
}

// ── Metadata → Tags (máx 4 tags por limitación del SDK) ───────────────────────
function metaToTags(metadata) {
  if (!metadata || typeof metadata !== 'object') return [];
  return Object.entries(metadata)
    .slice(0, 4)
    .map(([k, v]) => ({
      key:   k.replace('x-amz-meta-', '').slice(0, 32),
      value: String(v).slice(0, 256),
    }));
}

// ── Contenido mínimo válido para Greenfield ────────────────────────────────────
function ensureMinContent(content, objectName) {
  if (!content || content.trim().length < 32) {
    return `# Synergix | ${objectName} | ${new Date().toISOString()}\n${content || ''}`;
  }
  return content;
}

// ── Reed-Solomon checksums ────────────────────────────────────────────────────
async function buildChecksums(buffer) {
  const rs       = new ReedSolomon();
  const hashRes  = await rs.encode(Uint8Array.from(buffer));
  return hashRes.map(x => bytesFromBase64(x));
}

// ══════════════════════════════════════════════════════════════════════════════
// objectExists — HEAD request sin descargar contenido
// ══════════════════════════════════════════════════════════════════════════════
async function objectExists(objectName) {
  try {
    const res = await client.object.headObject(BUCKET_NAME, objectName);
    return !!(res && res.objectInfo);
  } catch (e) {
    return false;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// uploadToGreenfield — crea objeto nuevo
// ══════════════════════════════════════════════════════════════════════════════
async function uploadToGreenfield(content, userId, objectName, metadata) {
  // Auto-generar nombre si no se provee
  if (!objectName) {
    const now   = new Date();
    const month = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}`;
    objectName  = `aisynergix/aportes/${month}/${userId}_${Date.now()}.txt`;
  }

  content = ensureMinContent(content, objectName);

  // Escribir a archivo temporal
  const tmpPath = path.join(os.tmpdir(), `gf_${Date.now()}_${Math.random().toString(36).slice(2)}.txt`);
  fs.writeFileSync(tmpPath, content, 'utf8');

  try {
    const wallet     = getWallet();
    const fileBuffer = fs.readFileSync(tmpPath);
    const checksums  = await buildChecksums(fileBuffer);
    const sps        = await client.sp.getStorageProviders();
    if (!sps || sps.length === 0) throw new Error('No Storage Providers disponibles');
    const primarySP  = sps[0];
    const tags       = metaToTags(metadata);
    const visibility = getVisibility(objectName);

    // Crear objeto
    const createTx = await client.object.createObject({
      bucketName:       BUCKET_NAME,
      objectName,
      creator:          wallet.address,
      visibility,
      contentType:      'text/plain; charset=utf-8',
      redundancyType:   RedundancyType.REDUNDANCY_EC_TYPE,
      payloadSize:      Long.fromNumber(fileBuffer.length),
      expectChecksums:  checksums,
      expectCheckSums:  checksums,
      primarySpAddress: primarySP.operatorAddress,
      tags:             tags.length > 0 ? { tags } : undefined,
    });

    const txHash = await broadcastTx(createTx, wallet);

    // Upload contenido al SP
    await client.object.uploadObject(
      {
        bucketName: BUCKET_NAME,
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
    console.log(`✅ Creado: ${objectName} | tx: ${txHash.slice(0,16)}...`);
    return { success: true, cid: txHash, objectName, bucket: BUCKET_NAME };

  } catch (err) {
    if (fs.existsSync(tmpPath)) { try { fs.unlinkSync(tmpPath); } catch(_) {} }
    throw err;
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// updateObjectTags — actualiza solo los tags on-chain (MsgSetTag)
// Operación barata: no modifica el contenido del archivo.
// ══════════════════════════════════════════════════════════════════════════════
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
  console.log(`✅ Tags actualizados: ${objectName} | tx: ${txHash.slice(0,16)}...`);
  return { success: true, cid: txHash, objectName, updated: true };
}

// ══════════════════════════════════════════════════════════════════════════════
// deleteAndRecreate — borra y recrea con nuevo contenido
// ══════════════════════════════════════════════════════════════════════════════
async function deleteAndRecreate(content, objectName, metadata) {
  const wallet = getWallet();
  const delTx  = await client.object.deleteObject({
    bucketName: BUCKET_NAME,
    objectName,
    operator:   wallet.address,
  });
  await broadcastTx(delTx, wallet);
  console.log(`🗑️  Eliminado: ${objectName}`);
  // Esperar confirmación en la blockchain
  await new Promise(r => setTimeout(r, 3500));
  return await uploadToGreenfield(content, 'system', objectName, metadata);
}

// ══════════════════════════════════════════════════════════════════════════════
// upsertObject — CREATE o UPDATE automático
//
//   onlyTags=true  → si existe, solo actualiza tags (barato, sin gas de storage)
//   onlyTags=false → si existe, delete + recreate (más costoso)
// ══════════════════════════════════════════════════════════════════════════════
async function upsertObject(content, objectName, metadata, onlyTags = false) {
  const exists = await objectExists(objectName);

  if (!exists) {
    console.log(`📝 Creando: ${objectName}`);
    return await uploadToGreenfield(content, 'system', objectName, metadata);
  }

  if (onlyTags) {
    console.log(`🏷️  Tags only: ${objectName}`);
    return await updateObjectTags(objectName, metadata);
  }

  console.log(`🔄 Delete+Recreate: ${objectName}`);
  return await deleteAndRecreate(content, objectName, metadata);
}

// ── Exports ────────────────────────────────────────────────────────────────────
module.exports = {
  uploadToGreenfield,
  updateObjectTags,
  upsertObject,
  objectExists,
};

// ── Test directo desde CLI ─────────────────────────────────────────────────────
if (require.main === module) {
  const mode = process.argv[2] || 'test';
  const obj  = process.argv[3] || null;

  if (mode === 'test') {
    uploadToGreenfield(
      `# Synergix Test | ${new Date().toISOString()}\nTest upload from upload.js`,
      'test_user',
      null,
      { 'x-amz-meta-type': 'test', 'x-amz-meta-ts': String(Date.now()) }
    )
      .then(r  => { console.log('__RESULT__:' + JSON.stringify(r)); process.exit(0); })
      .catch(e => { console.error('__ERROR__:'  + e.message);        process.exit(1); });

  } else if (mode === 'exists' && obj) {
    objectExists(obj)
      .then(r  => { console.log('exists:', r); process.exit(0); })
      .catch(e => { console.error(e.message);  process.exit(1); });
  }
}
