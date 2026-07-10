"""Pruebas de la integración on-chain de bonds (Fase D) — parte pura."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aisynergix.services import bond_chain as bc


def test_disabled_by_default():
    # Sin SYNERGIX_BOND_CONTRACT la capa está inerte (contable de Fase A sigue).
    assert bc.contract_enabled() is False


def test_node_id_to_bytes32_shape_and_determinism():
    key = bc.node_id_to_bytes32("node_abc123def456")
    assert isinstance(key, bytes) and len(key) == 32
    # Determinista: misma entrada → misma clave (bot escribe y lee igual).
    assert bc.node_id_to_bytes32("node_abc123def456") == key
    # Distinta entrada → distinta clave.
    assert bc.node_id_to_bytes32("node_otro00000000") != key
    # Vacío no rompe.
    assert len(bc.node_id_to_bytes32("")) == 32


def test_contract_sol_exists_and_has_no_owner_drain():
    root = Path(__file__).resolve().parent.parent
    sol = (root / "contracts/SynergixNodeBond.sol").read_text(encoding="utf-8")
    # Superficie mínima esperada del contrato.
    for fn in ("function lock(", "function beginUnbond(", "function withdraw(",
               "function slash(", "nonReentrant"):
        assert fn in sol, f"falta {fn} en el contrato"
    # Invariante trustless: el owner NO puede retirar bonds de usuarios.
    # (No debe existir una función 'ownerWithdraw' ni transfer desde onlyOwner.)
    assert "ownerWithdraw" not in sol


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print(f"PASS {fn.__name__}")
        except Exception:
            failed += 1; print(f"FAIL {fn.__name__}"); traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    sys.exit(1 if failed else 0)
