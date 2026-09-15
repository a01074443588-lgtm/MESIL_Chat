from app.maintenance.benchmark_reviewed_audio_context import (
    infer_sample_service,
    normalized,
    recall,
    similarity,
)


def test_quality_metrics_ignore_spacing_but_not_missing_names() -> None:
    reference = "송영 후 가나다 어르신의 혈압을 확인함"
    same_content = "송영후 가나다 어르신 혈압 확인함"
    wrong_name = "송영후 라마바 어르신 혈압 확인함"

    assert normalized("송영 후") == "송영후"
    assert similarity(reference, same_content) > similarity(reference, wrong_name)
    assert recall(reference, same_content, ["가나다", "라마바"]) == 1.0
    assert recall(reference, wrong_name, ["가나다", "라마바"]) == 0.0


def test_whole_room_audio_is_grouped_by_names_from_one_service() -> None:
    names_by_service = {
        "facility": ["가나다"],
        "daycare": ["라마바", "사아자"],
        "homecare": ["차카타"],
    }

    assert infer_sample_service(
        "라마바 어르신과 사아자 어르신 송영 보고",
        None,
        names_by_service,
    ) == "daycare"
    assert infer_sample_service(
        "가나다 어르신과 라마바 어르신 보고",
        None,
        names_by_service,
    ) == "all"
    assert infer_sample_service(
        "이름 없는 일반 업무보고",
        "homecare",
        names_by_service,
    ) == "homecare"
