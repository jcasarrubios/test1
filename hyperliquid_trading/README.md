# hltrader — sistema de trading para Hyperliquid

Un sistema en Python para **desarrollar**, **testear** (backtest, optimización, walk-forward, replay en paper) y **ejecutar** (paper o live en testnet/mainnet) estrategias sobre los perpetuos de Hyperliquid.

Lo principal: la misma función `Strategy.signals()` alimenta al backtester y al motor en vivo, así que lo que pruebas es exactamente lo que ejecutas.

```
                ┌──────────────┐
  Hyperliquid ─▶│  data.py     │  velas (candleSnapshot), caché CSV, funding, datos sintéticos
   /info API    └──────┬───────┘
                       ▼
                ┌──────────────┐
                │ Strategy     │  df OHLCV ─▶ exposición objetivo en [-1, 1]
                └──┬────────┬──┘
         backtest  │        │  live / paper
                   ▼        ▼
      ┌──────────────┐   ┌──────────────┐    ┌──────────────┐
      │ backtest.py  │   │ runner.py    │───▶│ risk.py      │ límites + kill switch
      │ optimize.py  │   └──────┬───────┘    └──────────────┘
      └──────────────┘          ▼
                         ┌──────────────┐
                         │ brokers.py   │  PaperBroker | HyperliquidBroker (SDK oficial)
                         └──────────────┘
```

## Instalación

```bash
cd hyperliquid_trading
python -m venv .venv && source .venv/bin/activate
pip install -e ".[live,dev]"     # 'live' instala hyperliquid-python-sdk y eth-account
pytest                           # 38 tests, sin red
```

## Flujo de trabajo

### 1. Ver estrategias disponibles
```bash
hltrader strategies
```
Incluidas: `ema_cross` (tendencia), `donchian_breakout` (rupturas estilo Turtle), `rsi_reversion` y `bollinger_reversion` (reversión a la media).

### 2. Descargar datos
```bash
hltrader fetch --coin BTC --interval 1h --days 365       # se cachean en data/BTC_1h.csv
```
Nota: la API solo devuelve las ~5000 velas más recientes de cada intervalo. Si necesitas más historia, usa un intervalo mayor o trae tu propio CSV (`--csv`, columnas `time,open,high,low,close,volume`).

### 3. Backtest
```bash
hltrader backtest -s ema_cross --coin ETH --interval 4h --days 365 \
    -p fast=10 -p slow=50 --leverage 2 --stop-loss 0.05 --out runs/eth_ema
```
Supuestos del modelo:
- La señal calculada al cierre de la vela *t* se ejecuta en la apertura de *t+1*, así que no hay look-ahead.
- Comisión taker 0.045% (tier base de Hyperliquid) y slippage configurable (`--slippage-bps`).
- Stop-loss y take-profit se evalúan dentro de la vela con el high y el low. Si ambos se tocan en la misma vela, se asume que primero saltó el stop.
- Funding opcional (`--funding`, tasa horaria): los largos pagan si es positivo.
- Tamaño de la posición = `señal × leverage × equity`. Solo se opera cuando cambia la señal.

Métricas: retorno, CAGR, Sharpe, Sortino, Calmar, max drawdown, win rate, profit factor, exposición, comisiones y funding. Con `--out` se guardan `equity.csv`, `trades.csv` y `result.json`.

Si no hay red, usa `--synthetic 5000` para trabajar con velas sintéticas.

### 4. Optimización y validación
```bash
# Grid search
hltrader optimize -s donchian_breakout -g entry=20,40,55,80 -g exit=10,20 --metric sharpe

# Walk-forward: optimiza en el 70% de cada ventana y evalúa en el 30% siguiente
hltrader optimize -s ema_cross -g fast=5,10,20 -g slow=50,100,200 --walk-forward 4
```
El walk-forward sirve para detectar sobreajuste. Si el retorno out-of-sample es mucho peor que el in-sample, esos parámetros no son fiables.

### 5. Paper trading
```bash
# Replay acelerado sobre histórico, usando el mismo motor que en vivo
hltrader run -c configs/example.yaml --replay data/BTC_1h.csv

# Paper en tiempo real con precios reales (el estado se guarda en runs/paper_state.json)
hltrader run -c configs/example.yaml -v
```

### 6. Live
1. Crea una **API wallet** en app.hyperliquid.xyz → API. Esa clave firma órdenes pero no puede retirar fondos.
2. Exporta las credenciales. Nunca las subas al repo.
   ```bash
   export HL_PRIVATE_KEY=0x...          # clave de la API wallet
   export HL_ACCOUNT_ADDRESS=0x...      # dirección de la cuenta principal con los fondos
   ```
3. Empieza en **testnet** (es el valor por defecto; en app.hyperliquid-testnet.xyz hay faucet):
   ```bash
   hltrader run -c configs/example.yaml --mode live -v
   ```
4. Mainnet exige confirmarlo explícitamente:
   ```bash
   hltrader run -c configs/example.yaml --mode live --mainnet --i-understand-the-risks
   ```

El runner consulta el precio cada `poll_seconds`. Con ese precio comprueba stop-loss y take-profit, y en cada vela cerrada recalcula la señal. Si la señal cambia, envía una orden de mercado (IOC con slippage máximo). Todas las decisiones y fills quedan en `runs/journal.jsonl`.

### Gestión de riesgo (`risk:` en el YAML)
| Parámetro | Qué hace |
|---|---|
| `max_leverage` | Tope de notional / equity |
| `max_position_usd` | Tope de notional absoluto |
| `max_daily_loss` | Si la equity cae ese % en el día UTC, cierra la posición y detiene el bot |
| `min_order_usd` | Mínimo de Hyperliquid ($10) |
| `rebalance_tolerance` | Evita reajustes pequeños de tamaño |

## Crear tu propia estrategia

Crea `hltrader/strategies/mi_estrategia.py` e impórtalo en `hltrader/strategies/__init__.py`:

```python
import numpy as np
import pandas as pd
from ..indicators import ema, atr
from ..strategy import Strategy, register

@register
class MiEstrategia(Strategy):
    name = "mi_estrategia"
    description = "Largo cuando el precio supera la EMA + 1 ATR"
    defaults = {"n": 50, "k": 1.0}

    @property
    def warmup(self):
        return self.params["n"] * 3

    def generate(self, df: pd.DataFrame) -> pd.Series:
        band = ema(df["close"], self.params["n"]) + self.params["k"] * atr(df, 14)
        return pd.Series(np.where(df["close"] > band, 1.0, 0.0), index=df.index)
```

Reglas:
- Devuelve una exposición en `[-1, 1]`, que puede ser fraccional.
- Es **causal**: el valor en *t* solo puede usar datos hasta *t*. El test `test_signals_are_causal` lo comprueba automáticamente para cada estrategia registrada.

Después ya puedes usar `hltrader backtest -s mi_estrategia`, `optimize` y `run`.

## Estructura
```
hltrader/
  data.py          cliente /info, paginación, caché CSV, datos sintéticos, ReplaySource
  indicators.py    SMA, EMA, RSI, ATR, Bollinger, Donchian
  strategy.py      clase base + registro
  strategies/      estrategias incluidas
  backtest.py      motor de backtest (fees, slippage, SL/TP, funding, leverage)
  metrics.py       métricas de rendimiento
  optimize.py      grid search y walk-forward
  risk.py          límites y kill switch
  brokers.py       PaperBroker y HyperliquidBroker
  runner.py        bucle de ejecución paper/live
  cli.py           CLI `hltrader`
configs/example.yaml
tests/
```

> ⚠️ Esto es software, no asesoría financiera. Operar con apalancamiento puede hacerte perder todo el capital. Valida primero con backtest, luego walk-forward, luego paper y luego testnet, y solo después pasa a mainnet con un tamaño pequeño.
