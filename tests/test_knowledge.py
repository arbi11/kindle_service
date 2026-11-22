"""Evidence integrity and isolation for the local knowledge pilot."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from desktop.knowledge import load, graph, passages, PILOT_FILE, validate
from desktop.store import Store, private_write
from knowledge_fixture import populate, write


class KnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temporary.name))
        self.manifest = populate(self.store)

    def tearDown(self):
        self.temporary.cleanup()

    def test_pilot_is_optional_and_reading_remains_available(self):
        (self.store.root / PILOT_FILE).unlink()
        self.assertEqual(load(self.store), {"available": False})
        self.assertEqual(len(self.store.items()), 3)

    def test_shared_concepts_keep_identity_and_coverage_is_explicit(self):
        brain = load(self.store)
        self.assertEqual(brain["stats"], {"topics": 2, "concepts": 2, "insights": 2,
            "readings": 2, "library": 3, "unavailable_citations": 0})
        self.assertEqual([t["concept_ids"][0] for t in brain["topics"]], ["review", "review"])
        self.assertTrue(all(i["origin"] == "ai_draft" for i in brain["insights"]))

    def test_evidence_keeps_summary_and_original_separate(self):
        citations = load(self.store)["citations"]
        self.assertEqual([c["material_label"] for c in citations], ["Saved article", "Saved video summary"])
        self.assertEqual([c["content"] for c in citations], ["original", "summary"])
        self.assertEqual([c["has_original"] for c in citations], [True, False])
        self.assertTrue(all(c["available"] for c in citations))
        self.assertNotIn("body_html", json.dumps(citations))

    def test_reading_state_settings_and_content_do_not_change_when_loading(self):
        tables = ["items", "settings", "sources", "editions", "deliveries", "jobs"]
        before = {t: self.store.query("SELECT * FROM " + t) for t in tables}
        for _ in range(2):
            load(self.store)
        after = {t: self.store.query("SELECT * FROM " + t) for t in tables}
        self.assertEqual(before, after)

    def test_changed_source_disables_the_citation_instead_of_reusing_its_position(self):
        item = self.manifest["citations"][0]["item_id"]
        self.store.execute("UPDATE items SET body_html='<p>A different text.</p>' WHERE id=?", (item,))
        brain = load(self.store)
        citation = brain["citations"][0]
        self.assertFalse(citation["available"])
        self.assertIn("changed", citation["reason"])
        self.assertTrue(brain["citations"][1]["available"])
        self.assertEqual(brain["stats"]["unavailable_citations"], 1)

    def test_missing_source_retains_the_insight_and_reports_the_gap(self):
        item = self.manifest["citations"][0]["item_id"]
        self.store.execute("DELETE FROM items WHERE id=?", (item,))
        brain = load(self.store)
        self.assertEqual(len(brain["insights"]), 2)
        self.assertFalse(brain["citations"][0]["available"])
        self.assertIn("no longer", brain["citations"][0]["reason"])

    def test_wrong_passage_and_invented_quote_cannot_open_as_evidence(self):
        for field, value in [("paragraph", 1), ("quote", "A sentence the source never said.")]:
            with self.subTest(field=field):
                manifest = copy.deepcopy(self.manifest)
                manifest["citations"][0][field] = value
                write(self.store, manifest)
                citation = load(self.store)["citations"][0]
                self.assertFalse(citation["available"])
                self.assertIn("could not be located", citation["reason"])

    def test_broken_links_duplicate_ids_and_unlabelled_interpretations_are_rejected(self):
        cases = []
        missing = copy.deepcopy(self.manifest)
        missing["topics"][0]["concept_ids"] = ["nonexistent"]
        cases.append(missing)
        duplicate = copy.deepcopy(self.manifest)
        duplicate["concepts"].append(duplicate["concepts"][0])
        cases.append(duplicate)
        unlabelled = copy.deepcopy(self.manifest)
        unlabelled["insights"][0]["origin"] = "personal_note"
        cases.append(unlabelled)
        boolean_position = copy.deepcopy(self.manifest)
        boolean_position["citations"][0]["paragraph"] = True
        cases.append(boolean_position)
        for manifest in cases:
            with self.subTest(manifest=cases.index(manifest)), self.assertRaises(ValueError):
                validate(manifest)

    def test_bad_private_manifest_does_not_damage_reading(self):
        private_write(self.store.root / PILOT_FILE, "not JSON")
        with self.assertRaisesRegex(ValueError, "could not be read"):
            load(self.store)
        self.assertEqual(len(self.store.items()), 3)

    def test_nested_markup_uses_leaf_blocks_and_drops_active_content(self):
        self.assertEqual(passages('<script>bad()</script><h2>A title</h2><blockquote><p>One <b>claim</b>.</p></blockquote><ul><li>Another claim.</li></ul>'),
                         ["A title", "One claim.", "Another claim."])

    def test_graph_covers_unmapped_reading_without_inventing_semantic_edges(self):
        result = graph(self.store)
        self.assertEqual(result["stats"]["readings"], 3)
        self.assertEqual(result["stats"]["mapped"], 2)
        self.assertEqual(result["stats"]["unmapped"], 1)
        ids = {n["id"] for n in result["nodes"]}
        self.assertEqual(len(ids), len(result["nodes"]))
        self.assertTrue(all(e["from"] in ids and e["to"] in ids for e in result["edges"]))
        unmapped = next(n for n in result["nodes"] if n["type"] == "reading" and not n["mapped"])
        attached = [e for e in result["edges"] if unmapped["id"] in (e["from"], e["to"])]
        self.assertEqual([e["relation"] for e in attached], ["collected"])

    def test_connection_filter_removes_unrelated_ancestors_and_passages(self):
        result = graph(self.store, connection="youtube")
        self.assertEqual(result["stats"]["readings"], 1)
        self.assertEqual([n["label"] for n in result["nodes"] if n["type"] == "topic"], ["Knowledge"])
        self.assertEqual([c["id"] for c in result["citations"]], ["memory-source"])
        self.assertNotIn("concept:review", {n["id"] for n in result["nodes"]})

    def test_multiple_connections_combine_evidence_and_still_apply_dates(self):
        self.store.execute("UPDATE items SET published='2026-11-20T12:00:00+00:00'")
        before = self.store.query('SELECT * FROM items ORDER BY id')
        result = graph(self.store, connection='youtube,raindrop', start='2026-11-20', end='2026-11-20')
        self.assertEqual(result['stats']['readings'], 2)
        self.assertEqual(result['stats']['mapped'], 2)
        self.assertEqual({c['id'] for c in result['citations']}, {'memory-source', 'review-source'})
        self.assertEqual({n['connection_id'] for n in result['nodes'] if n['type'] == 'reading'}, {'youtube', 'raindrop'})
        self.assertEqual(graph(self.store, connection='youtube,raindrop', start='2026-11-21')['nodes'], [])
        self.assertEqual(self.store.query('SELECT * FROM items ORDER BY id'), before)

    def test_no_connections_means_empty_graph_without_ancestors_or_evidence(self):
        before = self.store.query('SELECT * FROM items ORDER BY id')
        result = graph(self.store, connection='none')
        self.assertEqual(result['stats']['readings'], 0)
        self.assertEqual(result['stats']['library'], 3)
        for field in ['nodes', 'edges', 'citations', 'insights', 'concepts', 'topics']:
            self.assertEqual(result[field], [])
        self.assertEqual(self.store.query('SELECT * FROM items ORDER BY id'), before)

    def test_date_bounds_are_inclusive_in_the_readers_timezone(self):
        self.store.execute("UPDATE items SET published='2026-07-01T00:00:00+00:00'")
        item = self.manifest["citations"][0]["item_id"]
        self.store.execute("UPDATE items SET published='2026-11-01T23:30:00+00:00' WHERE id=?", (item,))
        result = graph(self.store, start="2026-11-02", end="2026-11-02")
        self.assertEqual(result["stats"]["readings"], 1)
        self.assertEqual([n["published_day"] for n in result["nodes"] if n["type"] == "reading"], ["2026-11-02"])
        self.assertEqual([c["id"] for c in result["citations"]], ["review-source"])
        self.assertEqual(sum(n["id"] == "concept:review" for n in result["nodes"]), 1)
        self.assertEqual(graph(self.store, end="2026-11-01")["stats"]["readings"], 2)
        self.assertEqual(graph(self.store, connection="youtube", start="2026-11-02", end="2026-11-02")["nodes"], [])

    def test_invalid_dates_reversed_ranges_and_connections_are_rejected(self):
        for kwargs in [{"start": "2026-02-30"}, {"end": "yesterday"}, {"start": "2026-11-21", "end": "2026-11-20"}, {"connection": "bad connection"},
                       {'connection': 'youtube,,raindrop'}, {'connection': 'all,youtube'}, {'connection': 'youtube,none'}, {'connection': ','.join(['youtube'] * 51)}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                graph(self.store, **kwargs)

    def test_changed_evidence_loses_semantic_links_but_keeps_its_reading_node(self):
        item = self.manifest["citations"][0]["item_id"]
        self.store.execute("UPDATE items SET body_html='<p>A changed text.</p>' WHERE id=?", (item,))
        result = graph(self.store)
        self.assertEqual(result["stats"]["readings"], 3)
        self.assertEqual(result["stats"]["mapped"], 1)
        self.assertNotIn("concept:review", {n["id"] for n in result["nodes"]})
        self.assertTrue(all(c["id"] != "review-source" for c in result["citations"]))

    def test_reading_graph_works_without_concept_data_or_with_broken_concept_file(self):
        (self.store.root / PILOT_FILE).unlink()
        result = graph(self.store)
        self.assertEqual(result["stats"]["unmapped"], 3)
        self.assertEqual(result["stats"]["concepts"], 0)
        private_write(self.store.root / PILOT_FILE, "not JSON")
        result = graph(self.store)
        self.assertEqual(result["stats"]["readings"], 3)
        self.assertTrue(result["warning"])

    def test_graph_reaches_the_whole_library_and_does_not_write_reading(self):
        for n in range(505):
            self.store.add_item(kind="article", external_id=f"graph-history-{n}", title=f"Old reading {n}", connection_id="raindrop")
        before = self.store.query("SELECT * FROM items ORDER BY id")
        result = graph(self.store)
        self.assertEqual(result["stats"]["readings"], 508)
        self.assertEqual(sum(n["type"] == "reading" for n in result["nodes"]), 508)
        self.assertEqual(self.store.query("SELECT * FROM items ORDER BY id"), before)


if __name__ == "__main__":
    unittest.main()
