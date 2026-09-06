import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from ainglish.training_ingestion import cli, content_fingerprint, prepare_training_records, _json


class TrainingIngestionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "pack"
        (self.root / "data").mkdir(parents=True)
        self.register = [{"slug": "we", "split": "train", "status": "current", "kind": "grammatical",
            "ratified_version": "0.1.0", "ratified_at": "2026-08-01T00:00:00Z",
            "register_digest": "a" * 64, "content_digest": "b" * 64, "source_release_version": "3"}]
        one = {"id": "one", "slug": "we", "normative": True, "split": "train",
            "register_digest": "a" * 64, "source_release_version": "3",
            "prompt": "Who joins?", "response": "we-including-you\n  Full scope preserved."}
        self.rows = [one, dict(one, id="two"), dict(one, id="supplement", normative=False, response="Supplemental example.")]

    def pack(self, dataset="instruction", transform=None):
        files, counts = {}, {}
        for name, rows in [("register", self.register), (dataset, self.rows)]:
            raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode()
            rel = "data/" + name + ".jsonl"; (self.root / rel).write_bytes(raw)
            files[rel] = {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            counts[name] = len(rows)
        manifest = {"kind": "ainglish.language.training-pack", "license": "CC0-1.0", "splits": ["train"],
            "scope": "train-only-projection-of-frozen-language-release", "version": "3",
            "source": {"bundle": "ainglish-core-v3", "register_digest": "a" * 64}, "files": files, "counts": counts}
        if transform: transform(manifest)
        raw = json.dumps(manifest).encode(); (self.root / "MANIFEST.json").write_bytes(raw)
        return hashlib.sha256(raw).hexdigest()

    def read(self, **kwargs):
        return prepare_training_records(self.root, expected_manifest_sha256=self.pack(kwargs.get("dataset", "instruction")), **kwargs)

    def testExactDedupKeepsWholeContentAndEverySourceIdentity(self):
        result = self.read(); self.assertEqual(1, len(result["records"]))
        row = result["records"][0]
        self.assertEqual(self.rows[0], row["row"])
        self.assertEqual(["one", "two"], [r["id"] for r in row["provenance"]["source_rows"]])
        self.assertEqual(["exact_duplicate", "non_normative_excluded"], [r["reason"] for r in result["receipt"]["dropped"]])
        self.assertEqual(result, self.read())

    def testSupplementAndEvaluationFiltersRemainDistinct(self):
        result = self.read(include_non_normative=True)
        self.assertEqual(2, len(result["records"]))
        self.assertFalse(result["records"][1]["row"]["normative"])
        fingerprint = content_fingerprint("instruction", self.rows[0])
        result = self.read(evaluation_fingerprints=[fingerprint])
        self.assertEqual([], result["records"])
        self.assertEqual(2, sum(r["reason"] == "exact_evaluation_overlap" for r in result["receipt"]["dropped"]))
        with self.assertRaises(ValueError): self.read(slugs=["not-ratified"])

    def testEvenHashPinnedInconsistentSourcesAreRefused(self):
        rows, register = copy.deepcopy(self.rows), copy.deepcopy(self.register)
        for where, field, value in [("rows", "split", "test"), ("rows", "normative", "true"),
                ("rows", "slug", "proposal-only"), ("rows", "source_release_version", "4"),
                ("register", "kind", "protocol"), ("register", "status", "superseded"),
                ("register", "ratified_version", None), ("register", "register_digest", "c" * 64)]:
            self.rows, self.register = copy.deepcopy(rows), copy.deepcopy(register)
            getattr(self, where)[0][field] = value
            with self.subTest(where=where, field=field), self.assertRaises(ValueError): self.read()

    def testPinsCountsDuplicateIdsAndInvalidJsonFailClosed(self):
        pin = self.pack()
        with self.assertRaises(ValueError): prepare_training_records(self.root, expected_manifest_sha256="0" * 64)
        (self.root / "data/instruction.jsonl").write_bytes(b"{}\n")
        with self.assertRaises(ValueError): prepare_training_records(self.root, expected_manifest_sha256=pin)
        pin = self.pack(transform=lambda m: m["counts"].update(instruction=99))
        with self.assertRaises(ValueError): prepare_training_records(self.root, expected_manifest_sha256=pin)
        self.rows[1]["id"] = "one"
        with self.assertRaises(ValueError): self.read()
        for raw in ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}']:
            with self.assertRaises(ValueError): _json(raw)

    def testPretrainingDocumentsKeepInheritedTrainScopeButRefuseContradiction(self):
        self.rows = [{"id": "document-we", "text": "A complete frozen definition.", "metadata": {
            "slug": "we", "license": "CC0-1.0", "release_version": "3", "content_digest": "b" * 64, "register_digest": "a" * 64}}]
        result = self.read(dataset="pretrain_documents")
        self.assertEqual("train", result["records"][0]["provenance"]["split"])
        self.rows[0]["metadata"]["split"] = "holdout"
        with self.assertRaises(ValueError): self.read(dataset="pretrain_documents")
        self.rows[0]["metadata"].pop("split"); self.rows[0]["metadata"]["content_digest"] = "c" * 64
        with self.assertRaises(ValueError): self.read(dataset="pretrain_documents")

    def testParallelFieldsAndExactFingerprintDoNotNormalizeLanguage(self):
        self.rows = [dict(self.rows[0], ainglish="we-including-you", english="we, including you")]
        result = self.read(dataset="parallel"); self.assertEqual(1, len(result["records"]))
        row = self.rows[0]
        self.assertEqual(content_fingerprint("parallel", row), content_fingerprint("parallel", dict(row, id="other")))
        self.assertNotEqual(content_fingerprint("parallel", row), content_fingerprint("parallel", dict(row, ainglish="we including you")))

    def testCliWritesCheckableOutputWithoutOverwritingOrEditingThePack(self):
        pin = self.pack(); target = Path(self.temp.name) / "prepared"
        args = [str(self.root), "--manifest-sha256", pin, "--output", str(target)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, cli(args)); before = (target / "records.jsonl").read_bytes()
            self.assertEqual(2, cli(args)); self.assertEqual(before, (target / "records.jsonl").read_bytes())
            self.assertEqual(2, cli(args[:-1] + [str(self.root / "nested")]))
        receipt = json.loads((target / "RECEIPT.json").read_text())
        self.assertEqual(hashlib.sha256(before).hexdigest(), receipt["output_sha256"])
        self.assertEqual(pin, hashlib.sha256((self.root / "MANIFEST.json").read_bytes()).hexdigest())
        self.assertFalse((self.root / "nested").exists())


if __name__ == "__main__": unittest.main()
