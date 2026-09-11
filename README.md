# Vertex-bot ⚡

> **Vertex-bot**: Bot de alta performance para varredura on-chain, auditoria de segurança criptográfica, simulação e execução em DEXes.

---

## 📂 Context Stack (Engenharia de Contexto)

O projeto é governado por uma suíte de 6 documentos canônicos localizados no diretório [`context/`](./context), que servem como a "constituição" técnica do repositório:

1. [**`context/ARCHITECTURE.md`**](./context/ARCHITECTURE.md) — Arquitetura de microsserviços desacoplados e fluxos assíncronos (`asyncio`).
2. [**`context/TECH_STACK.md`**](./context/TECH_STACK.md) — Versões de interpretador (Python 3.11+), SDKs oficiais permitidos e modos de execução (Paper vs Live).
3. [**`context/DATA_SCHEMA.md`**](./context/DATA_SCHEMA.md) — Modelagem relacional SQLite (WAL mode) e structs/modelos Pydantic v2 em memória.
4. [**`context/SECURITY_RULES.md`**](./context/SECURITY_RULES.md) — Hard Gates de auditoria on-chain, preservação de capital e fórmulas de Break-Even e Trailing Stop.
5. [**`context/CODE_STANDARDS.md`**](./context/CODE_STANDARDS.md) — Diretrizes de Clean Code, 100% de tipagem estrita, tratamento granular de exceções e logging JSON.
6. [**`context/SYSTEM_PROMPT.md`**](./context/SYSTEM_PROMPT.md) — Prompt mestre de operação para inteligência artificial integrada.

---

## 🤖 Regras de IA e Governança

As diretrizes do projeto são automaticamente injetadas em assistentes de IA através dos arquivos de governança do workspace:
- [`GEMINI.md`](./GEMINI.md) / [`AGENTS.md`](./AGENTS.md)
- [`.agents/rules/context_stack.md`](./.agents/rules/context_stack.md)
