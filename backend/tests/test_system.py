from agents import SupportOrchestrator


def test_rag_query_returns_product_info():
    orchestrator = SupportOrchestrator()
    result = orchestrator.handle_query("What warranty does the WM-FL500 washer have?")

    assert result["routing"]["route"] == "rag"
    assert result["agent"] == "rag"
    assert "WM-FL500" in result["response"]


def test_auto_resolver_handles_error_code():
    orchestrator = SupportOrchestrator()
    result = orchestrator.handle_query("My washing machine won't drain and shows error E21")

    assert result["routing"]["route"] == "troubleshoot"
    assert result["agent"] == "auto_resolver"
    assert result["resolved"] is True
    assert "Drainage Restriction" in result["response"]


def test_auto_resolver_handles_cooling_issue():
    orchestrator = SupportOrchestrator()
    result = orchestrator.handle_query("My refrigerator is not cooling properly")

    assert result["agent"] == "auto_resolver"
    assert "airflow" in result["response"].lower()


def test_turboshoot_starts_for_complex_vibration_issue():
    orchestrator = SupportOrchestrator()
    result = orchestrator.handle_query(
        "My washing machine vibrates excessively even after leveling it and balancing the load"
    )

    assert result["agent"] == "turboshoot"
    assert result["conversation_id"]
    assert result["questions"]
    assert result["input_key"] == "recently_moved"


def test_turboshoot_continues_conversation():
    orchestrator = SupportOrchestrator()
    first = orchestrator.handle_query(
        "My washing machine vibrates excessively even after leveling it and balancing the load"
    )

    follow_up = orchestrator.continue_conversation(
        first["conversation_id"],
        {"recently_moved": "no"},
    )

    assert follow_up["agent"] == "turboshoot"
    assert follow_up["input_key"] == "load_type"
    assert "one more detail" in follow_up["message"].lower()
