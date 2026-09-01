"""Unit tests for the bounded LRU cache added for the online service."""

import unittest
from threading import Barrier, Thread

from autocomplete.cache import LruCache


class LruCacheTests(unittest.TestCase):
    def test_rejects_a_capacity_below_one(self) -> None:
        for capacity in (0, -1):
            with self.subTest(capacity=capacity):
                with self.assertRaisesRegex(ValueError, "capacity must be at least 1"):
                    LruCache(capacity)

    def test_reports_a_miss_for_an_unknown_key(self) -> None:
        cache: LruCache[str, int] = LruCache(2)

        self.assertEqual(cache.get("absent"), (False, None))

    def test_returns_a_stored_value(self) -> None:
        cache: LruCache[str, int] = LruCache(2)

        cache.put("python", 1)

        self.assertEqual(cache.get("python"), (True, 1))
        self.assertEqual(len(cache), 1)

    def test_distinguishes_a_stored_none_from_a_miss(self) -> None:
        cache: LruCache[str, None] = LruCache(2)

        cache.put("python", None)

        self.assertEqual(cache.get("python"), (True, None))

    def test_distinguishes_a_stored_empty_result_from_a_miss(self) -> None:
        cache: LruCache[str, tuple] = LruCache(2)

        cache.put("no matches", ())

        self.assertEqual(cache.get("no matches"), (True, ()))

    def test_replaces_the_value_of_an_existing_key_without_growing(self) -> None:
        cache: LruCache[str, int] = LruCache(2)

        cache.put("python", 1)
        cache.put("python", 2)

        self.assertEqual(cache.get("python"), (True, 2))
        self.assertEqual(len(cache), 1)

    def test_evicts_the_least_recently_used_key_when_full(self) -> None:
        cache: LruCache[str, int] = LruCache(2)

        cache.put("first", 1)
        cache.put("second", 2)
        cache.put("third", 3)

        self.assertEqual(len(cache), 2)
        self.assertEqual(cache.get("first"), (False, None))
        self.assertEqual(cache.get("second"), (True, 2))
        self.assertEqual(cache.get("third"), (True, 3))

    def test_reading_a_key_protects_it_from_the_next_eviction(self) -> None:
        cache: LruCache[str, int] = LruCache(2)
        cache.put("first", 1)
        cache.put("second", 2)

        cache.get("first")
        cache.put("third", 3)

        self.assertEqual(cache.get("first"), (True, 1))
        self.assertEqual(cache.get("second"), (False, None))

    def test_rewriting_a_key_protects_it_from_the_next_eviction(self) -> None:
        cache: LruCache[str, int] = LruCache(2)
        cache.put("first", 1)
        cache.put("second", 2)

        cache.put("first", 11)
        cache.put("third", 3)

        self.assertEqual(cache.get("first"), (True, 11))
        self.assertEqual(cache.get("second"), (False, None))

    def test_keeps_only_the_newest_entry_at_capacity_one(self) -> None:
        cache: LruCache[str, int] = LruCache(1)

        cache.put("first", 1)
        cache.put("second", 2)

        self.assertEqual(len(cache), 1)
        self.assertEqual(cache.get("first"), (False, None))
        self.assertEqual(cache.get("second"), (True, 2))

    def test_evicts_in_least_recently_used_order_over_a_long_run(self) -> None:
        cache: LruCache[int, int] = LruCache(3)
        for key in range(3):
            cache.put(key, key)

        # Touch 0 and 1 so 2 becomes the eviction candidate.
        cache.get(0)
        cache.get(1)
        cache.put(3, 3)
        cache.put(4, 4)

        self.assertEqual(len(cache), 3)
        self.assertEqual(cache.get(2), (False, None))
        self.assertEqual(cache.get(0), (False, None))
        self.assertEqual(cache.get(1), (True, 1))
        self.assertEqual(cache.get(3), (True, 3))
        self.assertEqual(cache.get(4), (True, 4))

    def test_clear_removes_every_entry(self) -> None:
        cache: LruCache[str, int] = LruCache(2)
        cache.put("first", 1)
        cache.put("second", 2)

        cache.clear()

        self.assertEqual(len(cache), 0)
        self.assertEqual(cache.get("first"), (False, None))

    def test_clear_leaves_the_cache_usable(self) -> None:
        cache: LruCache[str, int] = LruCache(2)
        cache.put("first", 1)

        cache.clear()
        cache.put("second", 2)

        self.assertEqual(cache.get("second"), (True, 2))
        self.assertEqual(len(cache), 1)

    def test_supports_tuple_keys_such_as_the_query_cache_key(self) -> None:
        cache: LruCache[tuple[str, int, int], tuple[str, ...]] = LruCache(2)

        cache.put(("python", 100, 4096), ("python documentation",))

        self.assertEqual(
            cache.get(("python", 100, 4096)),
            (True, ("python documentation",)),
        )
        self.assertEqual(cache.get(("python", 101, 4096)), (False, None))

    def test_never_exceeds_capacity_under_concurrent_writers(self) -> None:
        capacity = 8
        writer_count = 8
        writes_per_thread = 200
        cache: LruCache[int, int] = LruCache(capacity)
        start = Barrier(writer_count)
        observed_sizes: list[int] = []

        def hammer(worker: int) -> None:
            start.wait()
            for index in range(writes_per_thread):
                key = worker * writes_per_thread + index
                cache.put(key, key)
                cache.get(key)
                observed_sizes.append(len(cache))

        threads = [Thread(target=hammer, args=(worker,)) for worker in range(writer_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(len(cache), capacity)
        self.assertLessEqual(max(observed_sizes), capacity)

    def test_concurrent_readers_see_a_value_written_before_they_start(self) -> None:
        cache: LruCache[str, int] = LruCache(4)
        cache.put("shared", 7)
        reader_count = 8
        start = Barrier(reader_count)
        results: list[tuple[bool, int | None]] = []

        def reader() -> None:
            start.wait()
            results.append(cache.get("shared"))

        threads = [Thread(target=reader) for _ in range(reader_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(results, [(True, 7)] * reader_count)


if __name__ == "__main__":
    unittest.main()
