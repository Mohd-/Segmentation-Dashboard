# Segment Maturation and Execution System — v14

## What changed in v12

- Marking a prospect/lead as **Business Plan** now promotes it into the **Business Plan Execution** pipeline.
- The full lead-side technical summary is captured at the instant of promotion and retained as a frozen **Lead Summary** snapshot.
- In a promoted well's right-hand panel, use **Lead Summary** to show or hide that captured lead view. The normal Well Summary remains the active Business Plan summary.
- Promotion preserves all original lead tasks, task IDs, inputs, and history. BP tasks become operational and begin at **BP Execution Gate**.
- Reservoir CoS is ready for calculation with the approved `RF_model.joblib` model. Each row uses exactly:
  1. Pull-up
  2. Amplitude Ratio
  3. Base Tight Sarah (BTS)

  The model result is stored and displayed as a whole-number percent, for example `44%`.

## Add the Reservoir CoS model

Place the approved model file beside `main.py` and `database.py`:

```text
segment_maturation_execution_system_v12/
  RF_model.joblib
```

Or set a custom path before launching:

```bash
export SEGMENT_TRACKER_RF_MODEL_PATH="/secure/path/RF_model.joblib"
```

The application loads the model once per server process. Do not place untrusted joblib files in the application folder.

## Run locally

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r requirements.txt
```

3. Add the approved `RF_model.joblib` file if Reservoir CoS calculation is required.
4. Start the app:

```bash
python main.py
```

For shared deployment, run behind Gunicorn/Nginx and configure a production database strategy as documented in the technical review.

## v13 — Seal CoS calculation

The **Seal CoS** component now uses five technical inputs:

- Most recent age of activity
- Dip
- Azimuth vs. SHmax
- Fault Level of Confidence
- Fracture Permeability

The system calculates and stores **Seal CoS (%)** automatically when the component is saved:

- When **Most recent age of activity > 0.9**: `activity × fracture permeability`
- Otherwise (including `0.9`): `average(dip, azimuth vs. SHmax, fault level of confidence) × fracture permeability`

The result is displayed as a whole-number percentage, such as `44%`. Inputs should be entered as decimal factors used by the technical formula (for example, `0.44` for 44%).


## v14 — Lead mean gas in Well Summary

- **Mean PIIP Gas (BCF) — Lead Phase** is now shown in the right-hand Well Summary as soon as it is saved in **Lead Resource Assessment**.
- For a lead promoted to Business Plan, the same value is retained in the frozen **Lead Summary** view.


## v15 changes
- Seal and Reservoir CoS results display in calculated boxes beneath their input fields.
- Pull-up is now No / Semi / Yes. The RF model receives No=0, Semi=1, Yes=2.
- Toggling Business Plan on moves a Lead to BP Execution; toggling it off moves it back to Prospect Maturation without deleting BP work, inputs, lead summary, or history.

## v16: Automatic Presence CoS
Presence CoS is now calculated automatically and is read-only. The dashboard uses the final (last completed) Reservoir CoS row, Trap CoS, and Seal CoS:

`Presence CoS = Final Reservoir CoS × Trap CoS × Seal CoS`

Scores may be stored as decimals or whole percentages. The dashboard displays and stores the final result as a whole percentage. Source values and the calculation refresh automatically whenever Reservoir CoS, Trap CoS, or Seal CoS is saved.
