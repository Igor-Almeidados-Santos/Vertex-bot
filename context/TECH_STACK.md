# TECH_STACK.md — Especificações Tecnológicas e Ambiente

> **Sistema**: Vertex-bot  
> **Versão do Documento**: 1.0.0  
> **Objetivo**: Fixar as dependências permitidas, versões exatas do interpretador, restrições estritas de ambiente e parâmetros para o modo Paper Trading.

---

## 1. Interpretador e Runtime

| Componente | Especificação | Justificativa |
| :--- | :--- | :--- |
| **Linguagem** | Python | Ecossistema maduro em finanças quantitativas e Web3. |
| **Versão Mínima** | `3.11.0` (Recomendado: `3.11.8+` ou `3.12+`) | Suporte a `asyncio.TaskGroup`, melhorias de até 25% no throughput do interpretador e exception groups nativos. |
| **Gerenciador de Pacotes** | `poetry` ou `pip` com `pyproject.toml` | Lockfile determinístico e controle estrito de sub-dependências. |

---

## 2. Bibliotecas Oficiais Permitidas

> [!CAUTION]
> **Regra de Inclusão de Dependências**: É estritamente proibido importar pacotes externos que não constem nesta lista sem aprovação formal de arquitetura. O uso de bibliotecas depreciadas ou inventadas por IA resultará em falha no pipeline de CI/CD.

### 2.1. Concorrência e Comunicação de Rede
- **`asyncio`** (Standard Library): Controle central do Event Loop, filas (`Queue`), tarefas e primitivas de sincronização.
- **`aiohttp` (`>= 3.9.0`)**: Cliente HTTP assíncrono para chamadas REST a endpoints RPC, APIs de DEXes e agregadores de preço.
- **`websockets` (`>= 12.0`)**: Conexão persistente de baixa latência para streaming de logs de blocos e transações.

### 2.2. Integração Blockchain, Agregadores e Web3
- **Solana Stack**:
  - **`solders` (`>= 0.21.0`)**: Bindings de alta performance em Rust para parsing de transações, contas e chaves públicas da Solana.
  - **`solana` (`>= 0.34.0`)**: SDK oficial em Python para interação RPC com a rede Solana.
- **Roteamento de Execução e MEV (Solana DEXes)**:
  - **Jupiter v6 Swap API** (`https://quote-api.jup.ag/v6/quote` e `/swap`): Roteador e agregador oficial de liquidez da Solana (Raydium, Meteora, Orca) para obtenção de melhores rotas de swap e slippage mínimo.
  - **Jito Block Engine** (Transações via Bundles): Submissão direta a validadores com propina de prioridade (*tip*) e proteção nativa contra *frontrunning* e *sandwich attacks*.
- **Feeds Indexados de Alta Velocidade (Photon-Aligned)**:
  - **DexScreener API / Token Profiles** (`https://api.dexscreener.com`): Feed contínuo de novos pares, metadados enriquecidos e liquidez em USD.
  - **Helius Enhanced API & Webhooks**: Ingestão de eventos de transações de DEXes decodificados em tempo real.
- **EVM Stack** (se configurado para multichain):
  - **`web3.py` (`>= 6.15.0`)**: Interação assíncrona com nós EVM (`AsyncWeb3`, `AsyncHTTPProvider`, `WebSocketProvider`).
  - **`eth-account` (`>= 0.11.0`)**: Manipulação e assinatura de transações locais.

### 2.3. Modelagem de Dados, Validação e Tipagem
- **`pydantic` (`>= 2.6.0`)**: Validação de schemas, serialização/deserialização em alta velocidade (Rust core) e contratos de dados estritos.
- **`pydantic-settings` (`>= 2.2.0`)**: Parsing e validação de variáveis de ambiente (`.env`) com checagem de tipos em tempo de boot.

### 2.4. Armazenamento e Persistência
- **`aiosqlite` (`>= 0.19.0`)**: Driver SQLite assíncrono com suporte a transações não-bloqueantes no event loop.
- **`sqlite3`** (Standard Library via aiosqlite): Banco local embutido em modo WAL (Write-Ahead Logging).

### 2.5. Cálculos Financeiros e Precisão
- **`decimal`** (Standard Library): **Obrigatório** para todo cálculo financeiro, saldos, preços e divisões de supply. É proibido usar `float` puro para representação contábil e ordens de trade devido a erros de ponto flutuante.

### 2.6. Qualidade, Testes e Linting
- **`pytest` (`>= 8.0.0`)**: Framework de testes automatizados.
- **`pytest-asyncio` (`>= 0.23.0`)**: Suporte a testes de corrotinas e fixtures assíncronas.
- **`ruff` (`>= 0.3.0`)**: Linter e formatador de código ultrarrápido (substitui flake8, isort, black).
- **`mypy` (`>= 1.8.0`)**: Verificador estático de tipos com configuração estrita (`--strict`).

---

## 3. Ambientes de Execução: Paper Trading vs Live Trading

O bot implementa uma interface agnóstica de execução (`IExecutionEngine`), operando em dois modos mutuamente exclusivos:

### 3.1. Paper Trading Mode (Simulação / Homologação)
- **Ativação**: Variável de ambiente `EXECUTION_MODE=PAPER`.
- **Comportamento**:
  - **Zero Exposição de Chaves**: Nenhuma chave privada com fundos reais é exigida no ambiente. Utiliza uma chave pública arbitrária ou gerada em memória (`Keypair()`).
  - **Preços Reais**: As cotações e reservas de liquidez são consultadas em tempo real na blockchain via nós RPC reais.
  - **Simulação de Latência**: Injeta um atraso programático (configurável: 150ms a 400ms) antes de confirmar o preenchimento, reproduzindo a confirmação de bloco.
  - **Impacto e Slippage Virtual**: Modela a derrapagem de preço com base no volume da ordem versus a liquidez da pool detectada.
  - **Balanço Fictício**: Mantém uma carteira virtual no SQLite (ex: inicial de 10.0 SOL ou 1000 USDT) para contabilidade rigorosa de PnL e taxas estimadas.

### 3.2. Live Trading Mode (Execução Real)
- **Ativação**: Variável de ambiente `EXECUTION_MODE=LIVE`.
- **Requisitos de Segurança**:
  - `CONFIRM_LIVE_TRADING=true` explícito no `.env`.
  - Assinatura local estrita; chaves privadas nunca são logadas ou transmitidas em texto claro.
  - Verificação de saldo de reserva para taxas de prioridade.
  - Simulação de preflight RPC ativada antes da transmissão da transação.

---

## 4. Estrutura de Variáveis de Ambiente (`.env`)

A aplicação falhará imediatamente ao iniciar se qualquer variável obrigatória estiver ausente ou malformatada:

```bash
# === MODO DE OPERAÇÃO ===
EXECUTION_MODE=PAPER                     # PAPER | LIVE
CONFIRM_LIVE_TRADING=false               # Obrigatório true se LIVE
LOG_LEVEL=INFO                           # DEBUG | INFO | WARNING | ERROR

# === NÓS RPC & WEBSOCKETS ===
PRIMARY_RPC_HTTP_URL=https://api.mainnet-beta.solana.com
SECONDARY_RPC_HTTP_URL=https://solana-mainnet.g.alchemy.com/v2/YOUR-KEY
PRIMARY_RPC_WS_URL=wss://api.mainnet-beta.solana.com

# === BANCO DE DADOS ===
SQLITE_DB_PATH=data/vertex_bot.db

# === PAPER TRADING DEFAULTS ===
PAPER_INITIAL_BALANCE_SOL=10.0
PAPER_SIMULATED_LATENCY_MS=250

# === CARTEIRA (APENAS LIVE TRADING) ===
# WALLET_PRIVATE_KEY_BASE58=...         # Nunca comitar no git
```
