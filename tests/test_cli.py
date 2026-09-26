"""D01: CLI 종료 코드와 JSON 출력, `python -m chart_emotion` 실행."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

from helpers import BATCHES, CHARTS, EXPERIMENTS, INVALID, MAPPINGS, RECORDINGS, ROOT, SRC, TempDirCase, run_cli


class CliExitCodeTest(TempDirCase):
    def test_help_exits_zero(self) -> None:
        code, out, _ = run_cli(["--help"])
        self.assertEqual(code, 0)
        self.assertIn("import-chart", out)
        code, out, _ = run_cli(["import-chart", "--help"])
        self.assertEqual(code, 0)
        self.assertIn("--revision", out)

    def test_missing_required_argument_exits_two(self) -> None:
        code, _, err = run_cli(["validate"])
        self.assertEqual(code, 2)
        self.assertIn("--workspace", err)

    def test_full_demo_sequence(self) -> None:
        ws = str(self.path("ws"))
        code, out, _ = run_cli(["init", ws])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["seeded_sources"], ["synthetic-demo"])

        code, out, _ = run_cli(["import-chart", str(CHARTS / "period_a.csv"), "--batch", str(BATCHES / "period_a.json"), "--workspace", ws])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["snapshot_id"], "demo-period-a-r1")

        code, out, _ = run_cli(["import-chart", str(CHARTS / "period_b.csv"), "--batch", str(BATCHES / "period_b.json"), "--workspace", ws])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["entries_inserted"], 5)

        code, out, _ = run_cli(["import-chart", str(CHARTS / "period_b.csv"), "--batch", str(BATCHES / "period_b.json"), "--workspace", ws])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["status"], "already_imported")

        code, out, _ = run_cli(["validate", "--workspace", ws])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["totals"]["entries_all_revisions"], 10)
        self.assertIn("가상", report["data_mode_notice"])

        code, out, _ = run_cli(["validate", "--workspace", ws, "--experiment", str(EXPERIMENTS / "demo_comparison.json")])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["ok"])

        # init 재실행: 자료 보존
        code, out, _ = run_cli(["init", ws])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["counts"]["entries"], 10)

    def test_uninitialized_workspace_exits_two(self) -> None:
        ws = str(self.path("nope"))
        code, out, err = run_cli(["validate", "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        payload = json.loads(err)
        self.assertEqual(payload["error"]["code"], "workspace_not_initialized")
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.path("nope").exists())

    def test_csv_and_batch_errors_exit_two(self) -> None:
        ws = str(self.path("ws"))
        run_cli(["init", ws])
        code, _, err = run_cli(["import-chart", str(INVALID / "duplicate_rank.csv"), "--batch", str(BATCHES / "period_c.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "csv_invalid")
        code, _, err = run_cli(["import-chart", str(INVALID / "missing_column.csv"), "--batch", str(BATCHES / "period_c.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "csv_header_invalid")
        code, _, err = run_cli(["import-chart", str(CHARTS / "period_a.csv"), "--batch", str(INVALID / "invalid_dates.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["details"][0]["code"], "date_order")
        code, _, err = run_cli(["import-chart", str(self.path("missing.csv")), "--batch", str(BATCHES / "period_a.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "csv_not_found")

    def test_revision_conflict_and_incomplete_exit_codes(self) -> None:
        ws = str(self.path("ws"))
        run_cli(["init", ws])
        code, out, _ = run_cli(["import-chart", str(CHARTS / "period_c_incomplete.csv"), "--batch", str(BATCHES / "period_c.json"), "--workspace", ws])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["snapshot_status"], "incomplete")
        code, _, err = run_cli(["import-chart", str(CHARTS / "period_c_corrected.csv"), "--batch", str(BATCHES / "period_c.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "snapshot_revision_required")
        code, out, _ = run_cli(["import-chart", str(CHARTS / "period_c_corrected.csv"), "--batch", str(BATCHES / "period_c.json"), "--workspace", ws, "--revision", "2"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["snapshot_id"], "demo-period-c-r2")

    def test_experiment_reference_errors_exit_two_with_report(self) -> None:
        ws = str(self.path("ws"))
        run_cli(["init", ws])
        code, out, err = run_cli(["validate", "--workspace", ws, "--experiment", str(EXPERIMENTS / "demo_comparison.json")])
        self.assertEqual(code, 2)
        report = json.loads(out)
        self.assertFalse(report["ok"])
        self.assertEqual([e["code"] for e in report["errors"]], ["snapshot_not_found", "snapshot_not_found"])
        code, out, err = run_cli(["validate", "--workspace", ws, "--experiment", str(EXPERIMENTS / "invalid_unknown_label.json")])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "experiment_config_invalid")

    def test_hostile_inputs_exit_two_not_one(self) -> None:
        ws = str(self.path("ws"))
        run_cli(["init", ws])
        # 실험 설정: 유효한 demo_comparison 에 "top_n": 5 를 한 번 더 붙인 중복 키
        original = (EXPERIMENTS / "demo_comparison.json").read_text(encoding="utf-8").rstrip().rstrip("}")
        dup = self.path("dup_exp.json")
        dup.write_text(original + ',\n  "top_n": 5\n}\n', encoding="utf-8")
        code, out, err = run_cli(["validate", "--workspace", ws, "--experiment", str(dup)])
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(err)["error"]["code"], "json_duplicate_key")
        # 실험 설정: coverage_threshold = 10**400
        huge = self.path("huge_exp.json")
        text = (EXPERIMENTS / "demo_comparison.json").read_text(encoding="utf-8")
        self.assertIn('"coverage_threshold": 0.8', text)
        huge.write_text(text.replace('"coverage_threshold": 0.8', '"coverage_threshold": 1' + "0" * 400), encoding="utf-8")
        code, out, err = run_cli(["validate", "--workspace", ws, "--experiment", str(huge)])
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(err)["error"]["code"], "experiment_config_invalid")
        self.assertNotIn("unexpected_error", err)
        # CSV: 5000자리 rank
        csv_path = self.path("huge.csv")
        csv_path.write_text("rank,title,artist,provider_track_id\n" + "1" * 5000 + ",가상곡,가수,demo-x\n", encoding="utf-8")
        code, out, err = run_cli(["import-chart", str(csv_path), "--batch", str(BATCHES / "period_a.json"), "--workspace", ws])
        self.assertEqual(code, 2, err)
        self.assertEqual(json.loads(err)["error"]["code"], "csv_invalid")
        self.assertNotIn("Traceback", err)
        code, out, _ = run_cli(["validate", "--workspace", ws])
        self.assertEqual(json.loads(out)["totals"]["snapshots"], 0)

    def test_d05_cli_sequence_and_exit_codes(self) -> None:
        ws = str(self.path("ws"))
        run_cli(["init", ws])
        for period in ("a", "b"):
            run_cli(["import-chart", str(CHARTS / f"period_{period}.csv"), "--batch", str(BATCHES / f"period_{period}.json"), "--workspace", ws])
        # 가사 출처 없이 등록 → 2, 아무것도 남지 않음
        code, _, err = run_cli(["import-recordings", str(RECORDINGS / "recordings.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "source_not_registered")
        code, out, _ = run_cli(["register-source", "--workspace", ws, "--source-id", "synthetic-lyrics", "--name", "가상 가사", "--kind", "lyrics", "--data-mode", "synthetic"])
        self.assertEqual(code, 0)
        code, out, _ = run_cli(["import-recordings", str(RECORDINGS / "recordings.json"), "--workspace", ws])
        self.assertEqual(code, 0)
        self.assertEqual(len(json.loads(out)["recordings"]["registered"]), 10)
        code, _, err = run_cli(["import-recordings", str(INVALID / "recordings_invalid.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "recordings_file_invalid")
        for name in ("period_a_initial", "period_b_initial"):
            code, out, _ = run_cli(["import-mappings", str(MAPPINGS / f"{name}.json"), "--workspace", ws])
            self.assertEqual(code, 0)
            self.assertEqual(len(json.loads(out)["appended"]), 5)
        code, _, err = run_cli(["import-mappings", str(INVALID / "mapping_lyric_mismatch.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "lyric_recording_mismatch")
        code, out, _ = run_cli(["import-mappings", str(MAPPINGS / "corrections.json"), "--workspace", ws])
        self.assertEqual(code, 0)
        code, _, err = run_cli(["import-mappings", str(MAPPINGS / "stale_conflict.json"), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "stale_base_revision")
        self.assertNotIn("Traceback", err)
        code, out, _ = run_cli(["show-mappings", "--workspace", ws, "--snapshot", "demo-period-b-r1", "--history"])
        self.assertEqual(code, 0)
        entry = json.loads(out)["snapshots"][0]["entries"][3]
        self.assertEqual([h["state"] for h in entry["history"]], ["candidate", "unresolved"])
        code, _, err = run_cli(["show-mappings", "--workspace", ws, "--snapshot", "ghost"])
        self.assertEqual(code, 2)
        code, out, _ = run_cli(["validate", "--workspace", ws, "--experiment", str(EXPERIMENTS / "demo_comparison.json")])
        self.assertEqual(code, 0)
        report = json.loads(out)
        self.assertEqual(report["matching"]["states"], {"confirmed": 9, "candidate": 0, "unresolved": 1, "unmapped": 0})
        self.assertEqual([w["code"] for w in report["warnings"]], ["matching_incomplete"])
        code, _, err = run_cli(["import-mappings", str(self.path("missing.json")), "--workspace", ws])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "json_not_found")

    def test_register_source_cli(self) -> None:
        ws = str(self.path("ws"))
        run_cli(["init", ws])
        code, _, err = run_cli(["register-source", "--workspace", ws, "--source-id", "real-x", "--name", "실제 예시", "--kind", "chart", "--data-mode", "real", "--status", "permitted", "--allow", "import"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(err)["error"]["code"], "source_spec_invalid")
        code, out, _ = run_cli(["register-source", "--workspace", ws, "--source-id", "real-x", "--name", "실제 예시", "--kind", "chart", "--data-mode", "real"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["source"]["status"], "pending")
        self.assertEqual(json.loads(out)["source"]["allowed_operations"], [])

    def test_python_module_entry_point_without_install(self) -> None:
        env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONIOENCODING="utf-8")
        proc = subprocess.run([sys.executable, "-m", "chart_emotion", "--help"], capture_output=True, text=True, env=env, cwd=str(ROOT), encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("chart-emotion", proc.stdout)
        ws = str(self.path("ws"))
        proc = subprocess.run([sys.executable, "-m", "chart_emotion", "init", ws], capture_output=True, text=True, env=env, cwd=str(ROOT), encoding="utf-8")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["schema_version"], 2)
        proc = subprocess.run([sys.executable, "-m", "chart_emotion", "validate", "--workspace", str(self.path("missing"))], capture_output=True, text=True, env=env, cwd=str(ROOT), encoding="utf-8")
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(json.loads(proc.stderr)["error"]["code"], "workspace_not_initialized")


if __name__ == "__main__":
    unittest.main()
