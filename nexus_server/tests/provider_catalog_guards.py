"""Shared Provider catalog query bounds; no commercial fixtures or endpoints."""
from apps.providers.models import ProviderAccount


class ProviderCatalogGuards:
    def assert_provider_detail_prefetch(self, account, sql):
        self.assertEqual(account.account_id, "p-23")
        runtime_queries = [row["sql"] for row in sql if 'FROM "providers_providerruntimeaccount"' in row["sql"] and 'FROM "providers_provideraccount"' not in row["sql"]]
        self.assertEqual(len(runtime_queries), 1)
        self.assertIn(account.pk.hex, runtime_queries[0].replace("-", ""))
        other = ProviderAccount.objects.get(account_id="p-24")
        self.assertNotIn(other.pk.hex, runtime_queries[0].replace("-", ""))

    def assert_provider_summary_queries(self, small, large, result):
        self.assertEqual(len(small), len(large))
        self.assertLessEqual(len(large), 12)
        self.assertNotIn("providers_providerruntimehealthcheck", " ".join(q["sql"] for q in large))
        self.assertEqual(len(result["items"]), 14)
        for item in result["items"]:
            self.assertEqual(item["model_count"], 1)
            self.assertEqual(item["models"], [])
            self.assertFalse(item["models_loaded"])
            self.assertEqual(item["health_history"], [])

    def assert_provider_detail_history(self, detail, queries):
        self.assertTrue(detail["models_loaded"])
        self.assertEqual(detail["model_count"], 1)
        self.assertEqual(detail["models"][0]["upstream_model_id"], "detail-model")
        self.assertEqual(len(detail["health_history"]), 8)
        history_sql = [q["sql"] for q in queries if 'FROM "providers_providerruntimehealthcheck"' in q["sql"]]
        self.assertEqual(len(history_sql), 1)
        self.assertIn("LIMIT 8", history_sql[0])
        self.assertNotIn("ROW_NUMBER", history_sql[0])
