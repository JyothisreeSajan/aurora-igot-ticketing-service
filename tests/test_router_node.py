from app.core.graph.nodes.router_node import router_node, route_decision, _resolve_subgraph

def test_resolve_subgraph():
    # Exact category-name matches
    assert _resolve_subgraph("certificate", []) == "certificate_subgraph"
    assert _resolve_subgraph("course", []) == "courses_subgraph"
    assert _resolve_subgraph("program", []) == "program_subgraph"
    assert _resolve_subgraph("login_issue", []) == "login_and_registration_subgraph"
    assert _resolve_subgraph("profile_update", []) == "profile_update_subgraph"
    assert _resolve_subgraph("ca_apar_issue", []) == "ca_apar_subgraph"
    assert _resolve_subgraph("organisation_request", []) == "organisation_subgraph"
    
    # Catch-all general categories
    assert _resolve_subgraph("mobile_application", []) == "mobile_application_subgraph"
    assert _resolve_subgraph("virtual_event", []) == "virtual_event_subgraph"
    assert _resolve_subgraph("general", []) == "general_query_subgraph"
    
    # Unmapped category fallback
    assert _resolve_subgraph("some_unknown_category", []) == "general_query_subgraph"

def test_router_node_continuation():
    # Continuation fast-path should preserve route_to and bypass other logic
    state = {
        "ticket_id": "test_123",
        "is_continuation": True,
        "route_to": "courses_subgraph",
        "category": "certificate",
        "confidence": 0.1,  # very low confidence, normally routes to human_queue
        "quality_reroute_count": 5  # high reroutes, normally routes to human_queue
    }
    new_state = router_node(state)
    assert new_state["route_to"] == "courses_subgraph"
    assert new_state["is_continuation"] is True
    assert "graph_plan" in new_state
    assert len(new_state["graph_plan"]) == 1

def test_router_node_max_quality_reroutes():
    # Reroutes >= 2 should escalate to human_queue
    state = {
        "ticket_id": "test_123",
        "is_continuation": False,
        "category": "certificate",
        "confidence": 0.9,
        "quality_reroute_count": 2
    }
    new_state = router_node(state)
    assert new_state["route_to"] == "human_queue"
    assert new_state["escalated_to_human"] is True
    assert "Quality gate failed 2 times" in new_state["escalation_reason"]

def test_router_node_low_confidence():
    # Confidence < 0.75 should route to human_queue
    state = {
        "ticket_id": "test_123",
        "is_continuation": False,
        "category": "certificate",
        "confidence": 0.74,
        "quality_reroute_count": 0
    }
    new_state = router_node(state)
    assert new_state["route_to"] == "human_queue"
    assert new_state["escalated_to_human"] is True
    assert "below threshold" in new_state["escalation_reason"]

def test_router_node_normal_routing():
    # Standard flow
    state = {
        "ticket_id": "test_123",
        "is_continuation": False,
        "category": "certificate",
        "confidence": 0.8,
        "quality_reroute_count": 0
    }
    new_state = router_node(state)
    assert new_state["route_to"] == "certificate_subgraph"
    assert new_state.get("escalated_to_human", False) is False

def test_route_decision():
    assert route_decision({"route_to": "courses_subgraph"}) == "courses_subgraph"
    assert route_decision({}) == "human_queue"
