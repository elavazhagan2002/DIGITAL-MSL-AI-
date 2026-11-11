"""Digital MSL - pluggable retina query, validation and channel-routing component.

Mount into a FastAPI app:
    from digital_msl.router import router
    app.include_router(router)

Run on its own:
    python -m digital_msl serve            # API on :8090
    python -m digital_msl ask --repository <id> "question"
"""
