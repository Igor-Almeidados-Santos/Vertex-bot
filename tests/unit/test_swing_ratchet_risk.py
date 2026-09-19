"""
Testes Unitários para a Estratégia SWING RATCHET (Catraca de Degraus / Step Trailing Stop).
Valida proteção progressiva de capital, elevação contínua de pisos garantidos e isolamento contra SCALP.
"""

from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from main import VertexBotOrchestrator
from src.config.settings import Settings
from src.database.models import ExecutionMode, PositionState, TokenMetadata
from src.engine.risk import RiskManager


def test_swing_ratchet_progression_and_floor_breach():
    """Valida a progressão pelos Degraus 1 (2x -> BE), 2 (3x -> 2x) e 3 (5x -> 3x)."""
    entry_price = Decimal("1.00")
    capital_usd = Decimal("100.00")
    tokens = Decimal("100.00")

    pos = PositionState(
        token_address="SwingToken111",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=tokens,
        allocated_capital_usd=capital_usd,
    )

    risk = RiskManager(
        swing_initial_stop_loss_pct=Decimal("0.20"),  # -20%
        swing_tier1_mult=Decimal("2.0"),  # 2x
        swing_tier2_mult=Decimal("3.0"),  # 3x
        swing_tier3_mult=Decimal("5.0"),  # 5x
        swing_trailing_drop_pct=Decimal("0.25"),  # -25%
    )

    assert pos.strategy_type == "SWING"
    assert pos.ratchet_tier == 0
    assert pos.ratchet_floor_price == Decimal("0.0")

    # 1. Preço sobe para 1.50 (não atinge Degrau 1) -> Não sai
    decision = risk.evaluate_price_tick(pos, Decimal("1.50"))
    assert decision is None
    assert pos.ratchet_tier == 0
    assert pos.ratchet_floor_price == Decimal("0.0")

    # 2. Preço atinge 2.00 (Degrau 1 / 2x / $y):
    # Em SWING, NÃO VENDE 50%! Apenas promove o piso para 1.00 (preço de entrada / BE seguro)
    decision = risk.evaluate_price_tick(pos, Decimal("2.00"))
    assert decision is None
    assert pos.ratchet_tier == 1
    assert pos.ratchet_floor_price == Decimal("1.00")
    assert pos.remaining_token_amount == Decimal("100.00")  # Posição permanece 100% ativa

    # 3. Preço oscila para 1.40 (acima do piso de 1.00) -> Mantém posição
    decision = risk.evaluate_price_tick(pos, Decimal("1.40"))
    assert decision is None

    # 4. Preço sobe e atinge 3.00 (Degrau 2 / 3x / $z):
    # O piso é elevado para 2.00 (Degrau 1, travando +100% de lucro líquido garantido!)
    decision = risk.evaluate_price_tick(pos, Decimal("3.00"))
    assert decision is None
    assert pos.ratchet_tier == 2
    assert pos.ratchet_floor_price == Decimal("2.00")

    # 5. Preço continua subindo e atinge 5.00 (Degrau 3 / 5x):
    # O piso é elevado para 3.00 (Degrau 2, travando +200% de lucro líquido garantido!)
    decision = risk.evaluate_price_tick(pos, Decimal("5.00"))
    assert decision is None
    assert pos.ratchet_tier == 3
    assert pos.ratchet_floor_price == Decimal("3.00")

    # 6. Preço recua para 3.50 -> Ainda acima do piso de 3.00 -> Mantém posição
    decision = risk.evaluate_price_tick(pos, Decimal("3.50"))
    assert decision is None

    # 7. Preço recua para 2.90 (abaixo do piso de 3.00) -> Dispara SWING_RATCHET_STOP!
    decision = risk.evaluate_price_tick(pos, Decimal("2.90"))
    assert decision is not None
    action, amount = decision
    assert action == "SWING_RATCHET_STOP"
    assert amount == Decimal("100.00")


def test_swing_emergency_stop_before_tier1():
    """Valida que antes de atingir o Degrau 1, o Swing aplica o Stop Loss inicial protetivo."""
    entry_price = Decimal("1.00")
    pos = PositionState(
        token_address="SwingToken222",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=Decimal("50.00"),
        allocated_capital_usd=Decimal("50.00"),
    )

    risk = RiskManager(
        swing_initial_stop_loss_pct=Decimal("0.20"),  # Stop em 0.80
    )

    # Queda para 0.85 (-15%) -> Não sai
    decision = risk.evaluate_price_tick(pos, Decimal("0.85"))
    assert decision is None

    # Queda para 0.79 (-21%) -> Dispara EMERGENCY_STOP
    decision = risk.evaluate_price_tick(pos, Decimal("0.79"))
    assert decision is not None
    action, amount = decision
    assert action == "EMERGENCY_STOP"
    assert amount == Decimal("50.00")


def test_swing_zero_initial_stop_loss_allows_unlimited_dip_pre_tier1():
    """Valida que quando o stop loss inicial do Swing é 0%, a posição tolera quedas profundas sem sair."""
    entry_price = Decimal("1.00")
    pos = PositionState(
        token_address="SwingMoonbag111",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=Decimal("100.00"),
        allocated_capital_usd=Decimal("100.00"),
    )

    assert pos.trailing_stop_price == Decimal("0.0")

    risk = RiskManager(
        swing_initial_stop_loss_pct=Decimal("0.0"),  # Stop loss inicial desativado!
        swing_tier1_mult=Decimal("2.0"),
    )

    # Tick 0 no preço de entrada (ou leve spread abaixo devido a slippage) -> Não sai!
    decision = risk.evaluate_price_tick(pos, Decimal("0.99"))
    assert decision is None
    assert pos.trailing_stop_price == Decimal("0.0")

    # Queda de -50% (cotação a 0.50) -> Não sai!
    decision = risk.evaluate_price_tick(pos, Decimal("0.50"))
    assert decision is None
    assert pos.ratchet_tier == 0
    assert pos.trailing_stop_price == Decimal("0.0")

    # Queda de -80% (cotação a 0.20) -> Não sai!
    decision = risk.evaluate_price_tick(pos, Decimal("0.20"))
    assert decision is None
    assert pos.remaining_token_amount == Decimal("100.00")

    # Recuperação estrondosa para 2.00 (Degrau 1 / 2x):
    decision = risk.evaluate_price_tick(pos, Decimal("2.00"))
    assert decision is None
    assert pos.ratchet_tier == 1
    assert pos.ratchet_floor_price == Decimal("1.00")
    assert pos.trailing_stop_price == Decimal("1.00")

    # Agora que alcançou Degrau 1, se cair para 0.95 (abaixo do piso garantido de 1.00), encerra a posição no BE!
    decision = risk.evaluate_price_tick(pos, Decimal("0.95"))
    assert decision is not None
    action, amount = decision
    assert action == "SWING_RATCHET_STOP"
    assert amount == Decimal("100.00")


def test_dual_track_coexistence():
    """Valida a coexistência simultânea de uma posição SCALP e uma posição SWING no mesmo par."""
    entry_price = Decimal("1.00")

    # Posição 1: SCALP
    pos_scalp = PositionState(
        token_address="DualTokenAAA",
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=entry_price,
        initial_token_amount=Decimal("50.00"),
        allocated_capital_usd=Decimal("50.00"),
    )

    # Posição 2: SWING
    pos_swing = PositionState(
        token_address="DualTokenAAA",
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=entry_price,
        initial_token_amount=Decimal("50.00"),
        allocated_capital_usd=Decimal("50.00"),
    )

    risk = RiskManager(
        break_even_gain_pct=Decimal("100.0"),  # 2x no scalp
        trailing_drop_pct=Decimal("0.12"),
        swing_tier1_mult=Decimal("2.0"),
    )

    # Cotação vai para 2.00 (2x / +100%)
    # 1. SCALP: deve disparar SCALP_TARGET_REACHED vendendo 100% dos tokens (50 tokens)
    decision_scalp = risk.evaluate_price_tick(pos_scalp, Decimal("2.00"))
    assert decision_scalp is not None
    action_scalp, amount_scalp = decision_scalp
    assert action_scalp == "SCALP_TARGET_REACHED"
    assert amount_scalp == Decimal("50.00")

    # 2. SWING: NÃO vende! Apenas eleva o piso da catraca para o preço de entrada
    decision_swing = risk.evaluate_price_tick(pos_swing, Decimal("2.00"))
    assert decision_swing is None
    assert pos_swing.ratchet_tier == 1
    assert pos_swing.ratchet_floor_price == Decimal("1.00")
    assert pos_swing.remaining_token_amount == Decimal("50.00")


@pytest.mark.asyncio
async def test_dual_track_entry_orchestration(tmp_path: Path) -> None:
    """Valida que o Orchestrator em modo DUAL divide o montante alocado em 2 ordens simultâneas (SCALP + SWING)."""
    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_dual.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        PAPER_INITIAL_WALLET_USD=Decimal("50.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("4.00"),
        MAX_CONCURRENT_POSITIONS=5,
    )
    orch = VertexBotOrchestrator(settings)
    orch.execution_engine.balance_usd = Decimal("50.00")
    orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

    token = TokenMetadata(
        address="DualTrackBuyToken11111111111111111111111",
        dex="raydium",
        initial_liquidity_usd=Decimal("25000.0"),
        symbol="DUAL",
        raw_event={
            "age_hours": 3.0,
            "pair_data": {
                "volume": {"h1": 30000.0},
                "priceChange": {"m5": 2.0},
                "txns": {"m5": {"buys": 50, "sells": 10}},
                "liquidity": {"usd": 25000.0},
            },
        },
    )

    pos_scalp = PositionState(
        id=1,
        token_address=token.address,
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("4.0"),
        allocated_capital_usd=Decimal("4.00"),
    )
    pos_swing = PositionState(
        id=2,
        token_address=token.address,
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("4.0"),
        allocated_capital_usd=Decimal("4.00"),
    )

    orch.execution_engine.execute_buy = AsyncMock(side_effect=[pos_scalp, pos_swing])  # type: ignore[method-assign]

    await orch._evaluate_and_execute_entry(token)

    # Verifica que foram disparadas 2 compras (uma SCALP e uma SWING) com o valor integral de $4.00 para cada perna
    assert orch.execution_engine.execute_buy.await_count == 2
    calls = orch.execution_engine.execute_buy.await_args_list
    assert calls[0].kwargs["amount_usd"] == Decimal("4.00")
    assert calls[0].kwargs["strategy_type"] == "SCALP"
    assert calls[1].kwargs["amount_usd"] == Decimal("4.00")
    assert calls[1].kwargs["strategy_type"] == "SWING"

    # Confirma que ambas foram registradas no position_tracker
    assert len(orch.position_tracker.active_positions) == 2
    active_strategies = {p.strategy_type for p in orch.position_tracker.active_positions.values()}
    assert active_strategies == {"SCALP", "SWING"}


@pytest.mark.asyncio
async def test_dual_mode_scalp_only_for_young_token(tmp_path: Path) -> None:
    """Verifica que um token com 50 minutos (cenário PONK) em modo DUAL abre APENAS Scalp e JAMAIS Swing."""
    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_ponk.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        PAPER_INITIAL_WALLET_USD=Decimal("50.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("2.00"),
        MAX_CONCURRENT_POSITIONS=10,
        MIN_TOKEN_AGE_HOURS_SCALP=2.0,
        MIN_TOKEN_AGE_HOURS_SWING=3.0,
    )
    orch = VertexBotOrchestrator(settings)
    orch.execution_engine.balance_usd = Decimal("50.00")
    orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

    token_ponk = TokenMetadata(
        address="PonkToken11111111111111111111111111111111111",
        dex="raydium",
        initial_liquidity_usd=Decimal("34000.0"),
        symbol="PONK",
        raw_event={
            "age_hours": 2.2,  # 2.2h de vida (>= 2h para Scalp, mas < 3h para Swing)
            "pair_data": {
                "volume": {"h1": 50000.0},
                "priceChange": {"m5": 1.0},
                "txns": {"m5": {"buys": 30, "sells": 5}},
                "liquidity": {"usd": 34000.0},
            },
        },
    )

    pos_scalp = PositionState(
        id=1,
        token_address=token_ponk.address,
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("2.0"),
        allocated_capital_usd=Decimal("2.00"),
    )

    orch.execution_engine.execute_buy = AsyncMock(return_value=pos_scalp)  # type: ignore[method-assign]

    await orch._evaluate_and_execute_entry(token_ponk)

    # Deve ter chamado execute_buy EXATAMENTE UMA VEZ para SCALP!
    assert orch.execution_engine.execute_buy.await_count == 1
    call = orch.execution_engine.execute_buy.await_args
    assert call.kwargs["strategy_type"] == "SCALP"
    assert call.kwargs["amount_usd"] == Decimal("2.00")

    # Apenas a posição de Scalp foi registrada
    assert len(orch.position_tracker.active_positions) == 1
    pos = list(orch.position_tracker.active_positions.values())[0]
    assert pos.strategy_type == "SCALP"


@pytest.mark.asyncio
async def test_dual_mode_scalp_only_for_old_token(tmp_path: Path) -> None:
    """Verifica que um token com 15 horas (> 4h) em modo DUAL abre APENAS Scalp e JAMAIS Swing."""
    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_old.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        PAPER_INITIAL_WALLET_USD=Decimal("50.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("2.00"),
        MAX_CONCURRENT_POSITIONS=10,
    )
    orch = VertexBotOrchestrator(settings)
    orch.execution_engine.balance_usd = Decimal("50.00")
    orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

    token_old = TokenMetadata(
        address="OldToken111111111111111111111111111111111111",
        dex="raydium",
        initial_liquidity_usd=Decimal("50000.0"),
        symbol="OLD",
        raw_event={
            "age_hours": 15.0,  # 15 horas (> 4h)
            "pair_data": {
                "volume": {"h1": 60000.0},
                "priceChange": {"m5": 0.5},
                "txns": {"m5": {"buys": 40, "sells": 10}},
                "liquidity": {"usd": 50000.0},
            },
        },
    )

    pos_scalp = PositionState(
        id=1,
        token_address=token_old.address,
        mode=ExecutionMode.PAPER,
        strategy_type="SCALP",
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("2.0"),
        allocated_capital_usd=Decimal("2.00"),
    )

    orch.execution_engine.execute_buy = AsyncMock(return_value=pos_scalp)  # type: ignore[method-assign]

    await orch._evaluate_and_execute_entry(token_old)

    assert orch.execution_engine.execute_buy.await_count == 1
    call = orch.execution_engine.execute_buy.await_args
    assert call.kwargs["strategy_type"] == "SCALP"


@pytest.mark.asyncio
async def test_dual_mode_swing_entry_when_scalp_slots_full(tmp_path: Path) -> None:
    """Verifica que quando as vagas de Scalp estão cheias (ex: 5/5), um token de Swing ainda consegue abrir vaga de Swing."""
    settings = Settings(
        SQLITE_DB_PATH=str(tmp_path / "test_slots.db"),
        EXECUTION_MODE="PAPER",
        TRADING_STRATEGY_MODE="DUAL",
        PAPER_INITIAL_WALLET_USD=Decimal("50.0"),
        PAPER_BUY_AMOUNT_USD=Decimal("1.00"),
        MAX_CONCURRENT_POSITIONS=10,  # 5 Scalp, 5 Swing
    )
    orch = VertexBotOrchestrator(settings)
    orch.execution_engine.balance_usd = Decimal("50.00")
    orch.chart_auditor.audit_token_pre_entry = AsyncMock(return_value=(True, None, {}))  # type: ignore[method-assign]

    # Pré-ocupa todos os 5 slots de SCALP
    for i in range(5):
        p = PositionState(
            id=i + 1,
            token_address=f"ScalpFullToken{i}",
            mode=ExecutionMode.PAPER,
            strategy_type="SCALP",
            entry_price=Decimal("1.0"),
            initial_token_amount=Decimal("1.0"),
            allocated_capital_usd=Decimal("1.00"),
        )
        orch.position_tracker.active_positions[p.id] = p

    # Chega um novo token maduro para Swing (3.0h de idade e $25k de liquidez)
    token_swing = TokenMetadata(
        address="SwingIdealToken1111111111111111111111111111",
        dex="raydium",
        initial_liquidity_usd=Decimal("25000.0"),
        symbol="SWNG",
        raw_event={
            "age_hours": 3.0,
            "pair_data": {
                "volume": {"h1": 30000.0},
                "priceChange": {"m5": 1.5},
                "txns": {"m5": {"buys": 50, "sells": 10}},
                "liquidity": {"usd": 25000.0},
            },
        },
    )

    pos_swing = PositionState(
        id=6,
        token_address=token_swing.address,
        mode=ExecutionMode.PAPER,
        strategy_type="SWING",
        entry_price=Decimal("1.0"),
        initial_token_amount=Decimal("1.0"),
        allocated_capital_usd=Decimal("1.00"),
    )
    orch.execution_engine.execute_buy = AsyncMock(return_value=pos_swing)  # type: ignore[method-assign]

    await orch._evaluate_and_execute_entry(token_swing)

    # Como as vagas de Scalp estão cheias (5/5), mas há vagas de Swing livres (0/5), abre exclusivamente SWING!
    assert orch.execution_engine.execute_buy.await_count == 1
    call = orch.execution_engine.execute_buy.await_args
    assert call.kwargs["strategy_type"] == "SWING"
    assert call.kwargs["amount_usd"] == Decimal("1.00")

