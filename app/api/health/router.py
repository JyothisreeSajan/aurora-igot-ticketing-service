from fastapi import APIRouter

from app.core.utils.es_utils import es_manager

router = APIRouter()

@router.get("")
@router.get("/")
async def simple_health_check():
    """Simple health check to verify the application is running."""
    return {"status": "ok", "message": "Application is running"}

@router.get("/detail")
async def detailed_health_check():
    """Detailed health check to verify connectivity to dependencies."""
    health_status = {
        "status": "ok",
        "dependencies": {}
    }



    # Check ElasticSearch
    try:
        if es_manager.client and es_manager.client.ping():
            health_status["dependencies"]["elasticsearch"] = "connected"
        else:
            health_status["dependencies"]["elasticsearch"] = "disconnected or ping failed"
            health_status["status"] = "error"
    except Exception as e:
        health_status["dependencies"]["elasticsearch"] = f"error: {e!s}"
        health_status["status"] = "error"

    return health_status
