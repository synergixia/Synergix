/**
 * aisynergix/scripts/init_bucket.js
 * ═══════════════════════════════════════════════════════════════════════════
 * Crea la estructura inicial del bucket synergix en BNB Greenfield.
 *
 * Ejecutar UNA SOLA VEZ al desplegar Synergix por primera vez:
 *   node aisynergix/scripts/init_bucket.js
 *
 * Estructura que crea en aisynergix/:
 *   aisynergix/SYNERGIXAI/Synergix_ia.txt   ← cerebro inicial
 *   aisynergix/users/.gitkeep               ← placeholder
 *   aisynergix/aportes/.gitkeep             ← placeholder
 *   aisynergix/logs/.gitkeep                ← placeholder
 *   aisynergix/backups/.gitkeep             ← placeholder
 *   aisynergix/discovery/.gitkeep           ← placeholder
 * ═══════════════════════════════════════════════════════════════════════════
 */

require('dotenv').config({ path: require('path').join(__dirname, '..', '..', '.env') });

const { uploadToGreenfield } = require('../backend/upload.js');

const INIT_BRAIN = `=== SYNERGIX COLLECTIVE BRAIN ===
Actualizado: ${new Date().toISOString()}
Aportes procesados: 0
Version: GENESIS

=== CONOCIMIENTO FUSIONADO ===
Synergix es la primera inteligencia colectiva descentralizada en BNB Greenfield.
El conocimiento de la comunidad se inmortaliza on-chain permanentemente.

=== INVENTARIO ===
(vacío — contribuye para ser el primero en la historia)
`;

const GITKEEP = `# Synergix — ${new Date().toISOString()}
# Esta carpeta es parte de la estructura de aisynergix/ en BNB Greenfield.
`;

async function initBucket() {
  console.log('🚀 Iniciando estructura aisynergix/ en Greenfield...\n');

  const objects = [
    {
      content:    INIT_BRAIN,
      name:       'aisynergix/SYNERGIXAI/Synergix_ia.txt',
      metadata: {
        'x-amz-meta-last-sync':    new Date().toISOString(),
        'x-amz-meta-vector-count': '0',
        'x-amz-meta-type':         'brain',
        'x-amz-meta-total-size':   '0',
      },
      description: '🧠 Cerebro inicial (GENESIS)',
    },
    {
      content:    GITKEEP,
      name:       'aisynergix/users/.init',
      metadata: { 'x-amz-meta-type': 'placeholder' },
      description: '👤 Carpeta users/',
    },
    {
      content:    GITKEEP,
      name:       'aisynergix/aportes/.init',
      metadata: { 'x-amz-meta-type': 'placeholder' },
      description: '📦 Carpeta aportes/',
    },
    {
      content:    GITKEEP,
      name:       'aisynergix/logs/.init',
      metadata: { 'x-amz-meta-type': 'placeholder', 'x-amz-meta-severity': 'info' },
      description: '📋 Carpeta logs/',
    },
    {
      content:    GITKEEP,
      name:       'aisynergix/backups/.init',
      metadata: { 'x-amz-meta-type': 'placeholder' },
      description: '💾 Carpeta backups/',
    },
    {
      content:    GITKEEP,
      name:       'aisynergix/discovery/.init',
      metadata: { 'x-amz-meta-type': 'placeholder', 'x-amz-meta-source': 'none' },
      description: '🌐 Carpeta discovery/',
    },
  ];

  let success = 0;
  let failed  = 0;

  for (const obj of objects) {
    try {
      console.log(`📤 Creando: ${obj.description}`);
      console.log(`   → ${obj.name}`);
      const result = await uploadToGreenfield(
        obj.content,
        'system',
        obj.name,
        obj.metadata
      );
      console.log(`   ✅ TX: ${result.cid}\n`);
      success++;
      // Esperar entre uploads para no saturar el SP
      await new Promise(r => setTimeout(r, 2000));
    } catch (err) {
      if (err.message && err.message.includes('already exists')) {
        console.log(`   ⚠️  Ya existe — skipping\n`);
      } else {
        console.error(`   ❌ Error: ${err.message}\n`);
        failed++;
      }
    }
  }

  console.log('═══════════════════════════════════════════');
  console.log(`✅ Completado: ${success} creados, ${failed} errores`);
  console.log('');
  console.log('Estructura aisynergix/ lista en DCellar/Greenfield:');
  console.log('  synergix/');
  console.log('  └── aisynergix/');
  console.log('      ├── SYNERGIXAI/Synergix_ia.txt  ← GENESIS 🧠');
  console.log('      ├── users/                       ← perfiles 👤');
  console.log('      ├── aportes/                     ← memoria inmortal 📦');
  console.log('      ├── logs/                        ← auditoría 📋');
  console.log('      ├── backups/                     ← snapshots 💾');
  console.log('      └── discovery/                   ← tendencias 🌐');
  console.log('');
  console.log('Siguiente paso: bash aisynergix/scripts/resucitar.sh');
}

initBucket().catch(err => {
  console.error('❌ init_bucket falló:', err.message);
  process.exit(1);
});
