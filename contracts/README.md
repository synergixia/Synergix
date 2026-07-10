# Synergix — Contratos on-chain (Fase D)

Bonds de nodo **trustless**: a diferencia del modelo contable de la Fase A
(donde el bot custodia el SYNERGIX bloqueado), aquí los tokens viven dentro de
`SynergixNodeBond.sol` y solo se mueven según las reglas del contrato. El
operador **no puede** tocar los bonds de los usuarios.

> ⚠️ **SIN AUDITAR.** `SynergixNodeBond.sol` custodia SYNERGIX real. **No lo
> despliegues en mainnet con fondos antes de una auditoría profesional.** Un
> bug en un contrato de staking es pérdida directa e irreversible de fondos.

## Qué hace el contrato

| Función | Quién | Efecto |
|---|---|---|
| `lock(nodeId, amount)` | staker (tras `approve`) | Bloquea el bond. Acredita el monto **realmente recibido** (maneja el tax). |
| `beginUnbond(nodeId)` | staker | Inicia el periodo de unbonding. |
| `withdraw(nodeId)` | staker | Devuelve el bond tras cumplirse el unbonding. |
| `slash(nodeId)` | slasher (gobernanza) | Penaliza el bond → `slashTreasury`. |
| `setSlasher / setSlashTreasury / transferOwnership` | owner | Administración de roles. |

**No hay** función para que el owner retire bonds de usuarios — esa ausencia
es lo que lo hace trustless.

## Seguridad ya incluida
- Solidity ^0.8.20 (checks de overflow nativos).
- Guard anti-reentrancy en `lock/withdraw/slash`.
- Patrón checks-effects-interactions (se hace `delete` antes de transferir).
- Sin ruta de rug del owner; el slash solo va a una tesorería fija.
- Manejo de **fee-on-transfer**: `lock` mide el saldo antes/después.

## ⚠️ El tax de SYNERGIX (crítico)
SYNERGIX cobra tax en cada transferencia. Si NO exentas de tax a la dirección
del contrato, el usuario paga el impuesto **dos veces** (al bloquear y al
retirar). **Antes de usar el contrato, exenta su dirección del tax desde el
contrato del token** (whitelist / `setExcludedFromFee`). Es imprescindible.

## Pasos antes de usarlo (responsabilidad del operador)
1. **Compilar** (Hardhat o Foundry). No hay toolchain de Solidity en este repo.
2. **Auditar** con un tercero profesional. Innegociable para mainnet.
3. **Desplegar** en BSC con `token` = SYNERGIX, `unbondPeriod` = 7 días
   (604800), `slasher` = tu multisig de gobernanza, `slashTreasury` = tu
   tesoro. Ideal: `owner` = multisig.
4. **Exentar del tax** la dirección del contrato en el token.
5. Poner `SYNERGIX_BOND_CONTRACT=<address>` en el `.env`. Con eso el bot
   consulta el estado del bond on-chain (lectura, ver `services/bond_chain.py`).

## Migración desde la Fase A (contable → on-chain)
La Fase A bloquea el bond como un tag en Irys (los tokens **no** se mueven).
La Fase D mueve los tokens **al contrato**. La ruta de escritura del bot
(approve + `lock`, `beginUnbond`, `withdraw` desde la wallet custodial) NO se
incluye activada: solo debe cablearse **después** de que el contrato esté
desplegado y auditado, para poder probarla contra una dirección real. Hasta
entonces sigue vigente el bond contable de la Fase A.

## Ejemplo de despliegue (Foundry, orientativo)
```bash
forge create contracts/SynergixNodeBond.sol:SynergixNodeBond \
  --rpc-url $BSC_RPC_URL --private-key $DEPLOYER_KEY \
  --constructor-args $SYNERGIX_TOKEN 604800 $GOV_MULTISIG $TREASURY
```
Tras desplegar: verifica el código fuente en BscScan y ejecuta la auditoría.
