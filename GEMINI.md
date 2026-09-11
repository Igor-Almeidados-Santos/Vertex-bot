# REGRAS E DIRETRIZES DO PROJETO VERTEX-BOT

> **ATENÇÃO IA / AGENTE**: Este projeto opera sob uma infraestrutura estrita de **Engenharia de Contexto (Context Stack)**.
> Antes de formular qualquer resposta, planejar qualquer alteração, implementar código ou refatorar módulos, você **DEVE OBRIGATORIAMENTE** consultar os 6 documentos mantidos no diretório [`context/`](file:///home/almeida/Documentos/GitHub/Vertex-bot/context).

---

## 1. Diretório Oficial de Contexto: `context/`

Todo o desenvolvimento deste bot deve aderir 100% aos seguintes manuais técnicos:

1. [**`context/ARCHITECTURE.md`**](file:///home/almeida/Documentos/GitHub/Vertex-bot/context/ARCHITECTURE.md)
   - Respeite o pipeline assíncrono desacoplado em 4 camadas (`Scanner` $\rightarrow$ `SecurityValidator` $\rightarrow$ `ExecutionEngine` $\rightarrow$ `RiskManager`).
   - Módulos comunicam-se via `asyncio.Queue` com backpressure (`maxsize`).
   - Isole falhas por par/tarefa; nunca bloqueie o loop de eventos.

2. [**`context/TECH_STACK.md`**](file:///home/almeida/Documentos/GitHub/Vertex-bot/context/TECH_STACK.md)
   - Runtime: Python 3.11+.
   - Bibliotecas aprovadas: `asyncio`, `aiohttp`, `websockets`, `solders`, `solana`, `web3.py`, `pydantic v2`, `aiosqlite`, `decimal`.
   - **Zero alucinação**: Jamais importe ou utilize bibliotecas não listadas ou funções inexistentes.
   - Suporte nativo e isolado para `PAPER_TRADING` (simulação) e `LIVE_TRADING`.

3. [**`context/DATA_SCHEMA.md`**](file:///home/almeida/Documentos/GitHub/Vertex-bot/context/DATA_SCHEMA.md)
   - Nomes de tabelas, colunas e tipos de dados no SQLite (`tokens_catalogados`, `posicoes`, `ordens_executadas`) são imutáveis.
   - PRAGMAs obrigatórios: WAL mode, busy_timeout=5000, synchronous=NORMAL, foreign_keys=ON.
   - Contratos em memória devem usar os modelos Pydantic v2 especificados (`TokenMetadata`, `SecurityAuditResult`, `PositionState`, `OrderExecution`).

4. [**`context/SECURITY_RULES.md`**](file:///home/almeida/Documentos/GitHub/Vertex-bot/context/SECURITY_RULES.md)
   - **Hard Gates eliminatórios**: Mint Authority revogada, Freeze Authority revogada, LP $\ge 98\%$ queimada/bloqueada, Top 10 holders $\le 15\%$, taxas $\le 3\%$, liquidez mínima $\ge \$5.000$.
   - **Matemática de Saída**:
     - *Break-Even*: Venda de 50% dos tokens ao atingir +100% (2x do preço de entrada) para retorno de 100% do capital.
     - *Trailing Stop*: Recuo de -12% a -15% a partir de `highest_price_seen`.
   - Slippage máximo de 1.5% a 2.0%.

5. [**`context/CODE_STANDARDS.md`**](file:///home/almeida/Documentos/GitHub/Vertex-bot/context/CODE_STANDARDS.md)
   - 100% de tipagem estrita com `mypy --strict`.
   - Proibição de `except: pass` e blocos de captura genéricos silenciosos.
   - Logging estruturado com contexto (`timestamp`, `module`, `event`, `token_address`).
   - Uso obrigatório de `Decimal` para valores monetários e financeiros. Proibido usar `float` em contabilidade/PnL.

6. [**`context/SYSTEM_PROMPT.md`**](file:///home/almeida/Documentos/GitHub/Vertex-bot/context/SYSTEM_PROMPT.md)
   - Atue sempre como Engenheiro de Software Sênior e Arquiteto Quantitativo.
   - Entregue sempre código completo, modular, testado e defensivo, sem placeholders preguiçosos (`# TODO`).

---

## 2. Checklist Obrigatório Pré-Execução

Antes de submeter código ou aprovar alterações:
- [ ] O módulo está desacoplado conforme `context/ARCHITECTURE.md`?
- [ ] As dependências estão autorizadas em `context/TECH_STACK.md`?
- [ ] Os campos e tipos batem com `context/DATA_SCHEMA.md`?
- [ ] As travas de segurança e proteção de capital de `context/SECURITY_RULES.md` foram respeitadas?
- [ ] Há anotações de tipo completas e tratamento granular de exceções segundo `context/CODE_STANDARDS.md`?
