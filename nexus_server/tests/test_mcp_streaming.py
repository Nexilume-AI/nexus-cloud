"""The ASGI transport must deliver events before a long-lived stream ends."""
import asyncio
import threading
import unittest

from apps.common.streaming import AsyncStreamBridge


class MCPStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_event_does_not_wait_for_stream_completion(self):
        release = threading.Event()
        closed = threading.Event()
        def source():
            try:
                yield b"event: endpoint\ndata: /messages/\n\n"
                release.wait(2)
                yield b"second"
            finally:
                closed.set()
        bridge = AsyncStreamBridge(source(), cancel=release.set)
        iterator = bridge.__aiter__()
        self.assertIn(b"endpoint", await asyncio.wait_for(anext(iterator), 0.5))
        self.assertFalse(release.is_set())
        await iterator.aclose()
        self.assertTrue(await asyncio.to_thread(closed.wait, 1))

    async def test_errors_are_propagated_and_source_closed(self):
        closed = threading.Event()
        def source():
            try:
                yield b"first"
                raise ValueError("stream failed")
            finally:
                closed.set()
        iterator = AsyncStreamBridge(source()).__aiter__()
        self.assertEqual(await anext(iterator), b"first")
        with self.assertRaisesRegex(ValueError, "stream failed"):
            await anext(iterator)
        self.assertTrue(closed.is_set())

    async def test_backpressure_is_bounded_and_cancellation_is_idempotent(self):
        produced = []
        cancelled = []
        closed = threading.Event()
        def source():
            try:
                for n in range(10000):
                    produced.append(n)
                    yield str(n).encode()
            finally:
                closed.set()
        bridge = AsyncStreamBridge(source(), cancel=lambda: cancelled.append(True), capacity=2)
        iterator = bridge.__aiter__()
        await anext(iterator)
        await asyncio.sleep(0.05)
        self.assertLessEqual(len(produced), 4)
        await iterator.aclose()
        bridge.close()
        self.assertTrue(await asyncio.to_thread(closed.wait, 1))
        self.assertEqual(cancelled, [True])

    async def test_idle_streams_do_not_block_each_other_or_event_loop(self):
        release = threading.Event()
        def source(marker):
            yield marker
            release.wait(2)
        iterators = [AsyncStreamBridge(source(marker), cancel=release.set).__aiter__()
                     for marker in (b"first", b"second")]
        try:
            values = await asyncio.wait_for(asyncio.gather(*(anext(i) for i in iterators)), 0.5)
            self.assertEqual(values, [b"first", b"second"])
            self.assertFalse(release.is_set())
        finally:
            for iterator in iterators:
                await iterator.aclose()

    async def test_cancel_pending_read_closes_source(self):
        release = threading.Event()
        closed = threading.Event()
        def source():
            try:
                release.wait(2)
                yield b"never delivered"
            finally:
                closed.set()
        iterator = AsyncStreamBridge(source(), cancel=release.set).__aiter__()
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0.02)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(await asyncio.to_thread(closed.wait, 1))


if __name__ == "__main__":
    unittest.main()
