from graph import build_graph, TOTAL_OVERS

app = build_graph()

for phase, label in (("first", "First innings"), ("chase", "Second innings")):
    result = {}

    for over in range(1, TOTAL_OVERS + 1):
        question = f"{label}, go to over {over}"
        result = app.invoke({**result, "question": question})
        result = app.invoke({**result, "question": "who should bowl next?"})

        ctx = result.get("previous_context", {})
        rec = ctx.get("recommendation", "")
        provisional = ctx.get("provisional_bowlers", {})

        if provisional and any(name in rec for name in provisional):
            print(f"HIT: phase={phase} over={over} recommendation={rec!r} provisional={list(provisional)}")