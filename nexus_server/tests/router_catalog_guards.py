"""Shared Router catalog assertion, with each host's explicit query contract."""


class RouterCatalogGuards:
    def assert_router_catalog_queries(self, small, large, payload, expected=4):
        self.assertEqual(len(payload), 100)
        self.assertEqual(len(small), expected)
        self.assertEqual(len(large), expected)
