from canonical import compute_metrics


def test_transfer_semantics_and_distance_metrics_are_separate() -> None:
    payload = {
        "config": {"grid_pitch_x_um": 1.0, "grid_pitch_y_um": 1.0},
        "circuit": {"two_qubit_operation_count": 7, "one_qubit_operation_count": 3},
        "compiler": {
            "partitions": [{}, {}],
            "parallel_groups": [{}, {}, {}],
            "movement_transitions": [
                {
                    "batches": [
                        {
                            "moves": [
                                {"source": [0, 0], "destination": [3, 0]},
                                {"source": [0, 0], "destination": [0, 4]},
                            ]
                        },
                        {"moves": [{"source": [0, 0], "destination": [3, 4]}]},
                    ]
                }
            ],
        },
    }
    metrics = compute_metrics(payload)
    assert metrics["movement_batches"] == 2
    assert metrics["atom_endpoint_changes"] == 3
    assert metrics["transfer_rounds"] == 8
    assert metrics["atom_transfer_events"] == 12
    assert metrics["batch_critical_distance_um"] == 9.0
    assert metrics["endpoint_sum_distance_um"] == 12.0
