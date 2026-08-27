import os
import time
import logging
import threading
from pathlib import Path
import tempfile

logger = logging.getLogger(__name__)

# TTL for GLB files in minutes (Configurable constant)
GLB_RETENTION_MINUTES = 60

# The temp directory we use for uploads and GLB outputs
TEMP_DIR = Path(tempfile.gettempdir()) / "2d_to_3d_uploads"

# How often the cleanup task wakes up to check for expired files
CLEANUP_INTERVAL_SECONDS = 300  # 5 minutes

def cleanup_expired_glbs(temp_dir: Path, ttl_minutes: int) -> int:
    """
    Scans the temporary directory and deletes any .glb files that are older
    than the specified TTL.
    
    Returns the number of files deleted.
    """
    if not temp_dir.exists():
        return 0
        
    deleted_count = 0
    now = time.time()
    ttl_seconds = ttl_minutes * 60
    
    for item in temp_dir.glob("*.glb"):
        try:
            mtime = os.path.getmtime(item)
            if now - mtime > ttl_seconds:
                os.remove(item)
                deleted_count += 1
                logger.info(f"Cleaned up expired GLB file: {item.name}")
        except Exception as e:
            logger.warning(f"Failed to check or delete {item.name}: {e}")
            
    return deleted_count

def start_background_cleanup_task():
    """
    Starts a background daemon thread that periodically runs `cleanup_expired_glbs`.
    
    NOTE ON SCALABILITY (Single-Process vs Multi-Process):
    This threading-based background cleanup is designed for single-process deployment
    (e.g. running via `python run.py` or a single Gunicorn worker during development/testing).
    If this API is ever deployed in a multi-process/multi-worker environment (e.g. 
    multiple Gunicorn workers, uWSGI, or Kubernetes replicas sharing a volume), this 
    approach would lead to redundant/overlapping cleanup runs. 
    In production multi-process setups, this should be replaced with an external 
    scheduler (like Celery, a system cron job, or Kubernetes CronJob) or synchronized 
    with a distributed lock (e.g. Redis) to avoid race conditions.
    """
    def _run_periodically():
        while True:
            time.sleep(CLEANUP_INTERVAL_SECONDS)
            try:
                cleanup_expired_glbs(TEMP_DIR, GLB_RETENTION_MINUTES)
            except Exception as e:
                logger.error(f"Error in background cleanup task: {e}")
                
    # Create as a daemon thread so it dies automatically when the main app exits
    t = threading.Thread(target=_run_periodically, daemon=True, name="CleanupThread")
    t.start()
    logger.info(f"Started background GLB cleanup task (TTL={GLB_RETENTION_MINUTES}m)")
