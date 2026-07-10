// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/*
 * ⚠️  SIN AUDITAR — NO DESPLEGAR CON FONDOS REALES ANTES DE UNA AUDITORÍA
 *     PROFESIONAL.  Este contrato custodia SYNERGIX real; un bug = pérdida de
 *     fondos de los usuarios.  Ver contracts/README.md.
 */

interface IERC20 {
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function balanceOf(address account) external view returns (uint256);
}

/// @title  SynergixNodeBond — bonds de nodo trustless (Fase D)
/// @notice Bloquea SYNERGIX para crear un nodo; se retira tras un periodo de
///         unbonding, o lo penaliza (slash) la gobernanza por abuso.  A
///         diferencia del modelo contable, los tokens quedan gobernados por
///         el CÓDIGO, no por el operador: nadie puede moverlos salvo según
///         estas reglas.
/// @dev    Maneja el tax fee-on-transfer de SYNERGIX midiendo el monto REAL
///         recibido (balanceOf antes/después).  Recomendación fuerte: exentar
///         de tax a la dirección de este contrato en el token, para no cobrar
///         el impuesto dos veces (al bloquear y al retirar).
contract SynergixNodeBond {
    IERC20 public immutable token;        // SYNERGIX
    uint256 public immutable unbondPeriod; // segundos (p. ej. 7 días)

    address public owner;                  // admin (idealmente un multisig)
    address public slasher;                // puede penalizar (gobernanza/multisig)
    address public slashTreasury;          // destino de los bonds penalizados

    enum Status { None, Locked, Unbonding }
    struct Bond {
        address staker;
        uint256 amount;   // monto REALMENTE bloqueado (post-tax)
        uint64  unbondAt; // 0 mientras está Locked
        Status  status;
    }
    mapping(bytes32 => Bond) public bonds; // nodeId (bytes32) => Bond

    uint256 private _guard = 1;            // mutex anti-reentrancy (1=libre, 2=ocupado)

    event BondLocked(bytes32 indexed nodeId, address indexed staker, uint256 amount);
    event UnbondStarted(bytes32 indexed nodeId, uint64 unbondAt);
    event BondWithdrawn(bytes32 indexed nodeId, address indexed staker, uint256 amount);
    event BondSlashed(bytes32 indexed nodeId, uint256 amount, address treasury);
    event SlasherChanged(address indexed slasher);
    event SlashTreasuryChanged(address indexed treasury);
    event OwnershipTransferred(address indexed newOwner);

    modifier nonReentrant() {
        require(_guard != 2, "reentrant");
        _guard = 2;
        _;
        _guard = 1;
    }
    modifier onlyOwner() {
        require(msg.sender == owner, "not owner");
        _;
    }

    constructor(address token_, uint256 unbondPeriod_, address slasher_, address slashTreasury_) {
        require(token_ != address(0), "token zero");
        require(slasher_ != address(0), "slasher zero");
        require(slashTreasury_ != address(0), "treasury zero");
        token = IERC20(token_);
        unbondPeriod = unbondPeriod_;
        owner = msg.sender;
        slasher = slasher_;
        slashTreasury = slashTreasury_;
    }

    /// @notice Bloquea un bond para `nodeId`.  El staker debe `approve` antes.
    /// @dev    Acredita el monto REALMENTE recibido (maneja fee-on-transfer).
    function lock(bytes32 nodeId, uint256 amount) external nonReentrant {
        require(bonds[nodeId].status == Status.None, "node bonded");
        require(amount > 0, "zero amount");
        uint256 balBefore = token.balanceOf(address(this));
        require(token.transferFrom(msg.sender, address(this), amount), "transferFrom failed");
        uint256 received = token.balanceOf(address(this)) - balBefore;
        require(received > 0, "nothing received");
        bonds[nodeId] = Bond({staker: msg.sender, amount: received, unbondAt: 0, status: Status.Locked});
        emit BondLocked(nodeId, msg.sender, received);
    }

    /// @notice Inicia el unbonding (solo el staker; solo si está Locked).
    function beginUnbond(bytes32 nodeId) external {
        Bond storage b = bonds[nodeId];
        require(b.status == Status.Locked, "not locked");
        require(b.staker == msg.sender, "not staker");
        b.status = Status.Unbonding;
        b.unbondAt = uint64(block.timestamp + unbondPeriod);
        emit UnbondStarted(nodeId, b.unbondAt);
    }

    /// @notice Retira el bond tras cumplirse el unbonding (checks-effects-interactions).
    function withdraw(bytes32 nodeId) external nonReentrant {
        Bond storage b = bonds[nodeId];
        require(b.status == Status.Unbonding, "not unbonding");
        require(b.staker == msg.sender, "not staker");
        require(block.timestamp >= b.unbondAt, "still unbonding");
        uint256 amount = b.amount;
        address staker = b.staker;
        delete bonds[nodeId];                       // efecto ANTES de la interacción
        require(token.transfer(staker, amount), "transfer failed");
        emit BondWithdrawn(nodeId, staker, amount);
    }

    /// @notice Penaliza un bond por abuso — solo la gobernanza (slasher).
    function slash(bytes32 nodeId) external nonReentrant {
        require(msg.sender == slasher, "not slasher");
        Bond storage b = bonds[nodeId];
        require(b.status != Status.None, "no bond");
        uint256 amount = b.amount;
        delete bonds[nodeId];
        require(token.transfer(slashTreasury, amount), "transfer failed");
        emit BondSlashed(nodeId, amount, slashTreasury);
    }

    // ── Admin (idealmente un multisig; considerar renunciar a owner) ────────
    function setSlasher(address s) external onlyOwner {
        require(s != address(0), "zero");
        slasher = s;
        emit SlasherChanged(s);
    }

    function setSlashTreasury(address trez) external onlyOwner {
        require(trez != address(0), "zero");
        slashTreasury = trez;
        emit SlashTreasuryChanged(trez);
    }

    function transferOwnership(address n) external onlyOwner {
        require(n != address(0), "zero");
        owner = n;
        emit OwnershipTransferred(n);
    }

    // Nota: NO existe ninguna función para que el owner retire bonds de los
    // usuarios.  Los fondos solo salen por withdraw (staker, tras unbonding) o
    // slash (slasher → treasury).  Eso es lo que lo hace trustless.

    // ── Vistas ──────────────────────────────────────────────────────────────
    function bondOf(bytes32 nodeId)
        external
        view
        returns (address staker, uint256 amount, uint64 unbondAt, uint8 status)
    {
        Bond memory b = bonds[nodeId];
        return (b.staker, b.amount, b.unbondAt, uint8(b.status));
    }

    function isLocked(bytes32 nodeId) external view returns (bool) {
        return bonds[nodeId].status != Status.None;
    }
}
