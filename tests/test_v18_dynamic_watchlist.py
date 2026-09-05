from app.repositories.equity_watchlist import EquityWatchlistRepository


def test_watchlist_without_database_uses_ephemeral_memory():
    repo = EquityWatchlistRepository()
    if not repo.database_url:
        status = repo.status()
        assert status.persistent is False
        assert status.backend == "MEMORY_EPHEMERAL"
