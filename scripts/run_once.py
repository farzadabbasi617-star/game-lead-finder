import asyncio
from app.collectors.orchestrator import run_collector
from app.db.session import SessionLocal, Base, engine
from app.repository import init_seed_data


async def main():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        init_seed_data(db)
        result = await run_collector(db, source='all', keyword_limit=5, city_limit=3, result_limit=8)
        print(result)
    finally:
        db.close()


if __name__ == '__main__':
    asyncio.run(main())
