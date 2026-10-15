from services.reasoning_harness import HarnessResult


def test_harness_result_exposes_created_files():
    result = HarnessResult(content="Listo", created_files=("/tmp/demo.py",))
    assert result.created_files == ("/tmp/demo.py",)


def test_chat_marker_pattern_supports_paths_with_spaces():
    import re

    answer = "Listo.\nARCHIVO_CREADO:/tmp/mi juego.py\n"
    paths = re.findall(r"(?m)^ARCHIVO_CREADO:(.+?)\s*$", answer)
    assert paths == ["/tmp/mi juego.py"]
