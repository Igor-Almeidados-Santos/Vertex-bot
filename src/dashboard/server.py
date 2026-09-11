"""
Servidor Web Assíncrono do Dashboard do Vertex-bot.
Construído com aiohttp.web para leitura concorrente em SQLite (WAL mode).
"""

import asyncio
from pathlib import Path

from aiohttp import web

from src.database.connection import DatabaseManager
from src.database.repository import OrdersRepository, PositionsRepository, TokensRepository
from src.utils.logger import setup_logger

logger = setup_logger("vertex.dashboard")

STATIC_DIR = Path(__file__).parent / "static"


class DashboardServer:
    """Gerenciador de rotas e ciclo de vida da API e interface do Dashboard."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db: DatabaseManager = db
        self.tokens_repo: TokensRepository = TokensRepository(db)
        self.positions_repo: PositionsRepository = PositionsRepository(db)
        self.orders_repo: OrdersRepository = OrdersRepository(db)

    async def handle_index(self, _request: web.Request) -> web.Response:
        """Serve a interface Single-Page Application (SPA)."""
        html_file = STATIC_DIR / "index.html"
        if not html_file.exists():
            return web.Response(
                text="<h1>Vertex-bot Dashboard</h1><p>Interface index.html não encontrada.</p>",
                content_type="text/html",
                status=404,
            )
        content = html_file.read_text(encoding="utf-8")
        return web.Response(text=content, content_type="text/html")

    async def handle_summary(self, _request: web.Request) -> web.Response:
        """Retorna resumo consolidado de métricas e KPIs."""
        try:
            tokens_summary = await self.tokens_repo.get_tokens_summary()
            pnl_summary = await self.positions_repo.get_pnl_summary()

            payload = {
                "status": "success",
                "data": {
                    "pnl": pnl_summary,
                    "scanner": tokens_summary,
                },
            }
            return web.json_response(payload)
        except Exception as exc:
            logger.error("Erro ao gerar resumo no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_positions(self, request: web.Request) -> web.Response:
        """Retorna posições recentes com detalhes contábeis."""
        try:
            limit_param = request.query.get("limit", "100")
            limit = int(limit_param) if limit_param.isdigit() else 100
            positions = await self.positions_repo.get_all_positions(limit=limit)

            return web.json_response({"status": "success", "data": positions})
        except Exception as exc:
            logger.error("Erro ao buscar posições no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_orders(self, request: web.Request) -> web.Response:
        """Retorna histórico cronológico de ordens executadas."""
        try:
            limit_param = request.query.get("limit", "200")
            limit = int(limit_param) if limit_param.isdigit() else 200
            orders = await self.orders_repo.get_recent_orders(limit=limit)

            return web.json_response({"status": "success", "data": orders})
        except Exception as exc:
            logger.error("Erro ao buscar ordens no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)

    async def handle_tokens(self, request: web.Request) -> web.Response:
        """Retorna tokens catalogados com filtros de status e busca."""
        try:
            limit_param = request.query.get("limit", "100")
            limit = int(limit_param) if limit_param.isdigit() else 100
            status_filter = request.query.get("status")
            search = request.query.get("search")

            tokens = await self.tokens_repo.get_recent_tokens(
                limit=limit,
                status_filter=status_filter,
                search=search,
            )

            return web.json_response({"status": "success", "data": tokens})
        except Exception as exc:
            logger.error("Erro ao buscar tokens no dashboard: %s", exc)
            return web.json_response({"status": "error", "message": str(exc)}, status=500)


def create_dashboard_app(db: DatabaseManager) -> web.Application:
    """Fábrica para instanciar a aplicação web com todas as rotas configuradas."""
    server = DashboardServer(db)
    app = web.Application()

    app.router.add_get("/", server.handle_index)
    app.router.add_get("/api/summary", server.handle_summary)
    app.router.add_get("/api/positions", server.handle_positions)
    app.router.add_get("/api/orders", server.handle_orders)
    app.router.add_get("/api/tokens", server.handle_tokens)

    return app


async def run_dashboard_server(
    db_path: str = "data/vertex_bot.db",
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Inicializa e executa o servidor web do Dashboard como processo independente."""
    db = DatabaseManager(db_path)
    await db.initialize()

    app = create_dashboard_app(db)
    runner = web.AppRunner(app)
    await runner.setup()

    bound_port = port
    for p in range(port, port + 20):
        try:
            site = web.TCPSite(runner, host, p)
            await site.start()
            bound_port = p
            break
        except OSError as exc:
            if exc.errno == 98 and p < port + 19:
                continue
            raise

    logger.info("==================================================")
    logger.info(" VERTEX-BOT DASHBOARD OPERACIONAL!")
    logger.info(" Acesse no navegador: http://localhost:%d", bound_port)
    logger.info("==================================================")

    # Mantém o servidor ativo até sinal de cancelamento
    stop_event = asyncio.Event()
    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()
        await db.close()
        logger.info("Dashboard finalizado.")
