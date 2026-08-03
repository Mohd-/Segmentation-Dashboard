from __future__ import annotations

import hashlib
import sqlite3

from conftest import create_project, get_task_by_name
from scripts import audit_tracked_items, migration_dryrun_v7


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_v7_dryrun_copy_devolve_and_remerge_preserve_projection(client, tmp_path):
    pid = create_project(client, "DRYRUN-V7-1")
    bp_pid = create_project(client, "DRYRUN-BP-V7-1", pipeline_type="bp")
    task = get_task_by_name(client, pid, "Lead Assessment")
    response = client.patch(f"/api/tasks/{task['task_id']}/dynamic-fields", json={"fields": {
        "p90_area_km2": "10", "p10_area_km2": "20",
        "reservoir_thickness_ft": "25", "formation_thickness_ft": "100",
        "grv_p90_thousand_acre_ft": "30", "grv_p10_thousand_acre_ft": "40",
        "polygons_surfaces_loaded": "1", "lead_piip_gas_mean": "12.5",
        "lead_resource_scenario": "dry_gas_high_pressure",
    }})
    assert response.status_code == 200
    bp_task = get_task_by_name(client, bp_pid, "Lead Assessment")
    response = client.patch(f"/api/tasks/{bp_task['task_id']}/dynamic-fields", json={"fields": {
        "p90_area_km2": "5", "p10_area_km2": "9", "bp_custom_key": "preserve-me",
    }})
    assert response.status_code == 200

    import migrations

    source = client.db_path
    before_digest = _digest(source)
    copy = tmp_path / "rehearsal.db"
    migration_dryrun_v7.copy_database(source, copy)
    assert _digest(source) == before_digest
    # A fresh database is stamped LATEST (v9 as of the lead-level priority
    # work); devolve accepts any v7-or-newer copy since the later steps never
    # change the merged Lead Assessment shape this tool inverts.
    assert migration_dryrun_v7.stored_version(copy) == migrations.LATEST_SCHEMA_VERSION
    assert migration_dryrun_v7.devolve_to_v6(copy) == 2

    # Exercise the real minimum-advanced merge rule, not merely the synthetic
    # fresh-v7 fallback where all four source rows inherit one status.
    conn = sqlite3.connect(copy)
    try:
        for name, status in {
            "Area Definition": "Approved",
            "Thickness Estimation": "Ready",
            "GRV Inputs": "In Progress",
            "Resource Assessment": "Approved",
        }.items():
            conn.execute("UPDATE project_tasks SET status = ? WHERE project_id = ? AND task_name = ?",
                         (status, pid, name))
        for name, status in {
            "Area Definition": "Approved",
            "Thickness Estimation": "Ready",
            "GRV Inputs": "Approved",
            "Resource Assessment": "Approved",
        }.items():
            conn.execute("UPDATE project_tasks SET status = ? WHERE project_id = ? AND task_name = ?",
                         (status, bp_pid, name))
        conn.commit()
    finally:
        conn.close()

    before = migration_dryrun_v7.snapshot(copy, pre=True)
    assert before[pid]["merged_status"] == "In Progress"
    assert before[bp_pid]["merged_status"] == "Ready"
    assert before[bp_pid]["fields"]["bp_custom_key"] == "preserve-me"
    assert before[pid]["percent"] == round(4 / 12 * 100, 1)
    migration_dryrun_v7.bootstrap(copy)
    after = migration_dryrun_v7.snapshot(copy, pre=False)

    assert after[pid]["merged_status"] == "In Progress"
    assert after[pid]["checkpoints"] == before[pid]["checkpoints"]
    assert after[pid]["percent"] >= before[pid]["percent"]
    assert after[pid]["active"] == 9
    assert after[bp_pid]["merged_status"] == before[bp_pid]["merged_status"]
    assert after[bp_pid]["fields"] == before[bp_pid]["fields"]
    assert after[bp_pid]["checkpoints"] == before[bp_pid]["checkpoints"]
    assert after[bp_pid]["percent"] == before[bp_pid]["percent"]
    assert after[bp_pid]["active"] == 15
    assert migration_dryrun_v7.render(before, after) == 0


def test_tracked_items_audit_accepts_nine_rows_and_twelve_projection(client, capsys):
    create_project(client, "AUDIT-V7-1")
    create_project(client, "AUDIT-BP-V7-1", pipeline_type="bp")
    assert audit_tracked_items.main([str(client.db_path), "--rehearse-v7"]) == 0
    output = capsys.readouterr().out
    assert "leads with exactly 9 active tasks" in output
    assert "projects with 24-row v7 template" in output
    assert "(BP: 1)" in output
    assert "completed tracked items (total)" in output
    assert "VERDICT: PASS" in output
