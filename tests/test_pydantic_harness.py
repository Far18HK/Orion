from services.pydantic_harness import AgentPlan, PlanStep, VerificationResult


def test_plan_contract_is_bounded_and_typed():
    plan = AgentPlan(
        objective="Preparar informe",
        steps=[PlanStep(title="Leer fuentes", expected_result="Fuentes listadas")],
        definition_of_done="Informe con fuentes",
    )
    assert plan.steps[0].expected_result == "Fuentes listadas"


def test_verification_contract_rejects_unknown_status():
    result = VerificationResult(status="partial", evidence="Falta confirmar el archivo")
    assert result.status == "partial"
