import asyncio

from main import LanguageLogicOptimizer


def test_followups_release_session_lock_after_all_messages():
    optimizer = object.__new__(LanguageLogicOptimizer)
    optimizer._reply_locks = {}

    async def run():
        key = "group:1"
        lock = asyncio.Lock()
        await lock.acquire()
        await optimizer._send_followups_and_release(key, lock, [], 0, 0)
        assert not lock.locked()
        assert key not in optimizer._reply_locks

    asyncio.run(run())
