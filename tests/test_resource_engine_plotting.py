from matplotlib.colors import to_hex

from resource_engine import calculate_resources, create_exceedance_figure


def test_plot_axis_labels_and_mean_box_color():
    result = calculate_resources(
        {
            "scenario": "dry_gas_high_pressure",
            "method": "grv",
            "grv_p90_thousand_acre_ft": 12.6,
            "grv_p10_thousand_acre_ft": 17.3,
            "seed": 10_000,
            "iterations": 1_000,
        }
    )
    fig = create_exceedance_figure(result)
    ax = fig.axes[0]

    assert ax.get_xlabel() == "Non-Associated Gas [BCF]"
    assert ax.get_ylabel() == "Probability"
    assert ax.get_ylim() == (0.0, 1.0)
    assert ax.get_xlim()[0] == 0.0
    assert ax.get_legend() is None

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    p50_text = next(text for text in ax.texts if text.get_text().startswith("P50"))
    mean_text = next(text for text in ax.texts if text.get_text().startswith("Mean"))
    assert to_hex(mean_text.get_bbox_patch().get_edgecolor()) == "#c62828"
    assert not p50_text.get_window_extent(renderer).overlaps(mean_text.get_window_extent(renderer))


def test_condensate_plot_axis_label_and_mean_box_color():
    result = calculate_resources(
        {
            "scenario": "condensate_field_b",
            "method": "grv",
            "grv_p90_thousand_acre_ft": 2.0,
            "grv_p10_thousand_acre_ft": 200.0,
            "seed": 10_000,
            "iterations": 1_000,
        }
    )
    fig = create_exceedance_figure(result, resource="condensate")
    ax = fig.axes[0]

    assert ax.get_xlabel() == "Condensate [MMSTB]"

    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    mean_text = next(text for text in ax.texts if text.get_text().startswith("Mean"))
    assert to_hex(mean_text.get_bbox_patch().get_edgecolor()) == "#2e7d32"
    other_texts = [text for text in ax.texts if not text.get_text().startswith("Mean")]
    assert not any(
        mean_text.get_window_extent(renderer).overlaps(text.get_window_extent(renderer)) for text in other_texts
    )
